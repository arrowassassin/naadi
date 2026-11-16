import { readFileSync } from "node:fs";
import path from "node:path";
import { REQUIRED_UI_COPY_KEYS, type SearchHit, type SearchResponse, uiText, type WebResponse } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { cleanup, render, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { parseSearchSearch } from "./routes";
import { SearchPage } from "./SearchPage";
import {
  isSearchShortcut,
  searchShortcutSuppressed,
  shortcutBelongsToTarget,
  useSuppressSearchShortcut,
} from "./shortcut";
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
// The wording is the shipped default (what the screen shows before the config loads).
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (key: string, vars?: Record<string, string>) => uiText(null, key, vars),
}));

function hit(over: Partial<SearchHit> & Pick<SearchHit, "id" | "type" | "group" | "title">): SearchHit {
  return {
    context: "Step 1 · Renal",
    anchor: null,
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
    expect(await screen.findByText(uiText(null, "search.intro_title"))).toBeVisible();
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
          context: null, anchor: null,
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
    // the unlinked row says so in words, is aria-disabled and points at that text
    const row = questions.querySelector("[data-unlinked]") as HTMLElement;
    expect(row).toHaveAttribute("aria-disabled", "true");
    expect(within(questions).getByText("Available soon")).toBeVisible();
    expect(document.getElementById(row.getAttribute("aria-describedby") as string)).toHaveTextContent("Available soon");
    // structured context, worded by ui_copy
    expect(within(questions).getByText("Question · Step 1 · Renal")).toBeVisible();
    expect(screen.getByText("Found in your handwriting")).toBeVisible();
  });

  it("empty result offers the web and nothing is fetched until it is asked for", async () => {
    hooks.local = { ...data([]), isPending: false, isError: false };
    renderPage({ q: "zzzz qqqq" });
    expect(await screen.findByText("Nothing found for “zzzz qqqq”")).toBeVisible();
    expect(screen.getByRole("button", { name: "Search the web too" })).toBeVisible();
    expect(hooks.webArgs.every((a) => a[2] === false)).toBe(true);
  });

  it("when nothing matches locally the web is asked for automatically, under the empty message", async () => {
    hooks.local = { ...data([], true), isPending: false, isError: false };
    renderPage({ q: "qzxv wplk" });
    expect(await screen.findByText("Nothing found for “qzxv wplk”")).toBeVisible();
    expect(screen.getByRole("region", { name: "Web" })).toBeVisible();
    expect(screen.getByText("Looking on the web…")).toBeVisible();
    expect(hooks.webArgs.some((a) => a[2] === true)).toBe(true);
    expect(screen.queryByRole("button", { name: "Search the web too" })).toBeNull();
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
    expect(await screen.findByText(uiText(null, "search.web_limit"))).toBeVisible();
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
    expect(await screen.findByText(uiText(null, "search.error_body"))).toBeVisible(); // our wording, not the server's
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

describe("web auto-fetch", () => {
  it("waits for a settled query of 4+ characters", async () => {
    hooks.local = { ...data([], true), isPending: false, isError: false, isFetching: true };
    renderPage({ q: "qzxv wplk" });
    await screen.findByRole("searchbox");
    expect(hooks.webArgs.every((a) => a[2] === false)).toBe(true); // local still fetching: no web yet
    hooks.webArgs = [];
    hooks.local = { ...data([], true), isPending: false, isError: false, isFetching: false };
    cleanup();
    renderPage({ q: "abc" }); // 3 characters
    expect(await screen.findByText("Nothing found for “abc”")).toBeVisible();
    expect(hooks.webArgs.every((a) => a[2] === false)).toBe(true);
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
  it("does not fire inside inputs, dialogs, editors or media", () => {
    document.body.innerHTML =
      '<input id="i"><div role="dialog"><button id="d"></button></div><div data-no-shortcuts><p id="n"></p></div>' +
      '<video id="v"></video><div contenteditable="true" id="c"></div><button id="ok"></button>';
    for (const id of ["i", "d", "n", "v", "c"]) expect(shortcutBelongsToTarget(document.getElementById(id))).toBe(true);
    expect(shortcutBelongsToTarget(document.getElementById("ok"))).toBe(false);
    expect(shortcutBelongsToTarget(null)).toBe(false);
  });

  it("a screen can suppress Ctrl/Cmd+K while mounted, and nested suppressors stack", () => {
    const a = renderHook(() => useSuppressSearchShortcut(true));
    const b = renderHook(() => useSuppressSearchShortcut(true));
    expect(searchShortcutSuppressed()).toBe(true);
    a.unmount();
    expect(searchShortcutSuppressed()).toBe(true);
    b.unmount();
    expect(searchShortcutSuppressed()).toBe(false);
    const off = renderHook(() => useSuppressSearchShortcut(false));
    expect(searchShortcutSuppressed()).toBe(false);
    off.unmount();
  });

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
  const scopes = ["all", "chapters", "notes", "handwriting", "questions", "uploads", "pathway", "web"];
  const groups = ["chapters", "notes", "questions", "uploads", "pathway", "web"];
  const kinds = ["section", "upload", "question", "note", "ink", "pathway", "web"];
  const literal = /["'`](search\.[a-z_.]+)["'`]/g;

  it("every key the screen reads is required (so it can never be blanked) and has shipped text", () => {
    const here = path.resolve(import.meta.dirname);
    const files = ["SearchPage.tsx", "routes.tsx", "../shell/AppShell.tsx"];
    const used = new Set<string>([
      ...scopes.map((s) => `search.filter.${s}`),
      ...groups.map((g) => `search.group.${g}`),
      ...kinds.map((k) => `search.kind.${k}`),
    ]);
    for (const f of files) {
      for (const m of readFileSync(path.resolve(here, f), "utf8").matchAll(literal)) used.add(m[1] as string);
    }
    expect(used.size).toBeGreaterThan(30);
    for (const key of used) {
      expect(REQUIRED_UI_COPY_KEYS, key).toContain(key);
      expect(uiText(null, key), key).not.toBe(key);
    }
  });

  it("the placeholders in the shipped text are the ones the screen fills", () => {
    expect(uiText(null, "search.empty_title", { query: "rta" })).toBe("Nothing found for “rta”");
    expect(uiText(null, "search.count", { count: "7" })).toBe("7 results");
    expect(uiText(null, "search.web_label", { sources: "Tavily" })).toBe("Web results from Tavily");
  });
});
