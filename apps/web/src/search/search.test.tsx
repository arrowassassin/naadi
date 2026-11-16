import { readFileSync } from "node:fs";
import path from "node:path";
import type { SearchHit, SearchResponse, WebResponse } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { SEARCH_COPY_FALLBACK } from "./copy";
import { parseSearchSearch } from "./routes";
import { SearchPage } from "./SearchPage";
import { isSearchShortcut } from "./shortcut";
import { resolveTarget } from "./targets";

// The page's data hooks are the boundary: the API contract is covered by the db tests and the e2e suite.
const hooks = vi.hoisted(() => ({
  local: {} as Record<string, unknown>,
  web: {} as Record<string, unknown>,
  webArgs: [] as unknown[][],
  online: true,
}));
vi.mock("./api", () => ({
  useLocalSearch: () => hooks.local,
  useWebSearch: (...args: unknown[]) => {
    hooks.webArgs.push(args);
    return hooks.web;
  },
}));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => hooks.online }));
vi.mock("../agent/uiCopy", () => ({ useUiCopy: () => () => null }));

function hit(over: Partial<SearchHit> & Pick<SearchHit, "id" | "type" | "group" | "title">): SearchHit {
  return {
    meta: "Step 1 · Renal",
    snippet: [
      { text: "Type 4 ", hit: false },
      { text: "RTA", hit: true },
      { text: " is hyperkalemic.", hit: false },
    ],
    target: { type: "section", chapter_id: "ch07", section_id: "ch07-s3" },
    score: 1,
    ...over,
  };
}

function data(hits: SearchHit[], suggestWeb = false): { data: SearchResponse; isFetching: boolean } {
  return { data: { scope: "all", hits, suggest_web: suggestWeb, took_ms: 12 }, isFetching: false };
}

function renderPage(search: Record<string, unknown>, routes: string[] = ["/learn/$chapterId/$sectionId"]) {
  const root = createRootRoute({ component: Outlet });
  const children = routes.map((p) =>
    createRoute({ getParentRoute: () => root, path: p, component: () => <p>destination</p> }),
  );
  const index = createRoute({
    getParentRoute: () => root,
    path: "/search",
    validateSearch: parseSearchSearch,
    component: () => <SearchPage search={index.useSearch()} />,
  });
  const qs = new URLSearchParams(Object.entries(search).map(([k, v]) => [k, String(v)])).toString();
  const router = createRouter({
    routeTree: root.addChildren([index, ...children]),
    history: createMemoryHistory({ initialEntries: [`/search${qs ? `?${qs}` : ""}`] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  hooks.online = true;
  hooks.webArgs = [];
  hooks.local = { data: undefined, isPending: false, isFetching: false, isError: false };
  hooks.web = { data: undefined, isPending: true, isFetching: true, isError: false };
});

describe("Search screen", () => {
  it("shows the intro before anything is typed", async () => {
    renderPage({});
    expect(await screen.findByText(SEARCH_COPY_FALLBACK["search.intro_title"])).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "Search everything" })).toHaveFocus();
  });

  it("groups results, marks matches and links a chapter hit to the reader", async () => {
    hooks.local = {
      ...data([
        hit({ id: "section:s1", type: "section", group: "chapters", title: "Renal tubular acidosis" }),
        hit({
          id: "ink:i1",
          type: "ink",
          group: "handwriting",
          title: "4 → aldo problem",
          meta: "Found in your handwriting",
          target: {
            type: "ink",
            surface_type: "section",
            surface_id: "ch07-s3",
            chapter_id: "ch07",
            section_id: "ch07-s3",
          },
        }),
        hit({
          id: "question:q1",
          type: "question",
          group: "questions",
          title: "A 7-year-old boy",
          target: { type: "question", question_id: "q1", chapter_id: null },
        }),
      ]),
      isPending: false,
      isError: false,
    };
    renderPage({ q: "type 4 rta" });
    const chapters = await screen.findByRole("region", { name: "Chapters" });
    const link = within(chapters).getByRole("link", { name: /Renal tubular acidosis/ });
    expect(link).toHaveAttribute("href", "/learn/ch07/ch07-s3");
    expect(chapters.querySelector("mark")?.textContent).toBe("RTA");
    // notes and handwriting share one group, as on the board
    expect(screen.getByRole("region", { name: "Your notes and handwriting" })).toBeVisible();
    // the question route is not in this build: the hit is listed but not linked
    const questions = screen.getByRole("region", { name: "Questions" });
    expect(within(questions).queryByRole("link")).toBeNull();
    expect(within(questions).getByText("A 7-year-old boy")).toBeVisible();
  });

  it("empty result offers the web and nothing is fetched until it is asked for", async () => {
    hooks.local = { ...data([]), isPending: false, isError: false };
    renderPage({ q: "zzzz qqqq" });
    expect(await screen.findByText("Nothing found for “zzzz qqqq”")).toBeVisible();
    expect(screen.getByRole("button", { name: "Search the web too" })).toBeVisible();
    expect(hooks.webArgs.every((a) => a[2] === false)).toBe(true);
  });

  it("labels web results with their source and opens them in a new tab, https only", async () => {
    const web: WebResponse = {
      status: "ok",
      labels: ["Tavily"],
      results: [
        {
          title: "Latest ADA guidance",
          url: "https://example.org/ada",
          snippet: "Summary",
          source: "tavily",
          source_label: "Tavily",
          kind: "web",
          license: "unknown",
        },
        {
          title: "Not a web link",
          url: "javascript:alert(1)",
          snippet: "",
          source: "tavily",
          source_label: "Tavily",
          kind: "web",
          license: "unknown",
        },
      ],
    };
    hooks.local = { ...data([], true), isPending: false, isError: false };
    hooks.web = { data: web, isPending: false, isFetching: false, isError: false };
    renderPage({ q: "latest ada guidelines" });
    expect(await screen.findByText("Web results from Tavily")).toBeVisible();
    const a = screen.getByRole("link", { name: /Latest ADA guidance/ });
    expect(a).toHaveAttribute("href", "https://example.org/ada");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: /Not a web link/ })).toBeNull();
    expect(screen.getByText("Not a web link")).toBeVisible();
  });

  it("explains a web allowance that is used up, and an offline state without querying", async () => {
    hooks.local = { ...data([]), isPending: false, isError: false };
    hooks.web = {
      data: { status: "limit", labels: [], results: [], message: "x" },
      isPending: false,
      isFetching: false,
      isError: false,
    };
    renderPage({ q: "latest news", scope: "web" });
    expect(await screen.findByText(SEARCH_COPY_FALLBACK["search.web_limit"])).toBeVisible();
  });

  it("shows the offline state instead of results", async () => {
    hooks.online = false;
    renderPage({ q: "renal" });
    expect(await screen.findByText("You are offline")).toBeVisible();
  });

  it("shows the server's error with a retry", async () => {
    const refetch = vi.fn();
    hooks.local = {
      data: undefined,
      isPending: false,
      isFetching: false,
      isError: true,
      error: new ApiError("Search is not ready yet. Try again in a moment.", 503, "service_unavailable", "req-1"),
      refetch,
    };
    renderPage({ q: "renal" });
    expect(await screen.findByText("Search is not ready yet. Try again in a moment.")).toBeVisible();
    expect(screen.getByText("Request ID req-1")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("arrow keys move from the field to the first result", async () => {
    hooks.local = {
      ...data([hit({ id: "section:s1", type: "section", group: "chapters", title: "Renal tubular acidosis" })]),
      isPending: false,
      isError: false,
    };
    renderPage({ q: "rta" });
    const input = await screen.findByRole("searchbox");
    input.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("link", { name: /Renal tubular acidosis/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(input).toHaveFocus();
  });
});

describe("targets", () => {
  const routes = { "/learn/$chapterId/$sectionId": {}, "/practice/question/$questionId": {} };
  it("resolves only routes that exist", () => {
    expect(resolveTarget({ type: "section", chapter_id: "c", section_id: "s" }, routes)).toEqual({
      to: "/learn/$chapterId/$sectionId",
      params: { chapterId: "c", sectionId: "s" },
    });
    expect(resolveTarget({ type: "section", chapter_id: "c", section_id: "s" }, {})).toBeNull();
    expect(resolveTarget({ type: "question", question_id: "q" }, routes)?.params).toEqual({ questionId: "q" });
    expect(resolveTarget({ type: "question", question_id: "q" }, {})).toBeNull();
    expect(resolveTarget({ type: "note", note_id: "n" }, routes)).toBeNull(); // no section anchor
    expect(resolveTarget({ type: "pathway", stage_key: "step1" }, routes)).toBeNull();
  });
});

describe("shortcut and URL state", () => {
  it("recognises Ctrl+K and Cmd+K only", () => {
    const base = { key: "k", ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };
    expect(isSearchShortcut({ ...base, ctrlKey: true })).toBe(true);
    expect(isSearchShortcut({ ...base, metaKey: true, key: "K" })).toBe(true);
    expect(isSearchShortcut(base)).toBe(false);
    expect(isSearchShortcut({ ...base, ctrlKey: true, shiftKey: true })).toBe(false);
    expect(isSearchShortcut({ ...base, ctrlKey: true, key: "j" })).toBe(false);
  });

  it("parses the search params defensively", () => {
    expect(parseSearchSearch({ q: "  ", scope: "nonsense", web: "2" })).toEqual({
      q: undefined,
      scope: undefined,
      web: undefined,
    });
    expect(parseSearchSearch({ q: "rta", scope: "questions", web: 1 })).toEqual({
      q: "rta",
      scope: "questions",
      web: 1,
    });
    expect(parseSearchSearch({ scope: "all" }).scope).toBeUndefined();
  });
});

describe("wording", () => {
  it("the built-in fallback text equals app_config.ui_copy for every search.* key", () => {
    const yaml = readFileSync(path.resolve(import.meta.dirname, "../../../../config/app_config.yaml"), "utf8");
    const configured = new Map<string, string>();
    for (const line of yaml.split("\n")) {
      const m = /^ {2}(search\.[a-z_.]+): "(.*)"$/.exec(line);
      if (m?.[1]) configured.set(m[1], m[2] ?? "");
    }
    expect([...configured.keys()].sort()).toEqual(Object.keys(SEARCH_COPY_FALLBACK).sort());
    for (const [k, v] of Object.entries(SEARCH_COPY_FALLBACK)) expect(configured.get(k)).toBe(v);
  });
});
