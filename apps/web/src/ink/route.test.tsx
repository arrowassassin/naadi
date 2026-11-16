import { client } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { inkLabRoute } from "./route";

// The gate's inputs: the auth state and /api/me. The lab itself is replaced by a marker (its engine
// needs a real canvas, which the e2e suite covers).
const auth = vi.hoisted(() => ({ state: { status: "loading" } as Record<string, unknown> }));
const me = vi.hoisted(() => ({ result: {} as Record<string, unknown> }));
vi.mock("../auth/session", () => ({ useAuth: () => auth.state }));
vi.mock("../auth/api", () => ({ useMe: () => me.result }));
// ui_copy comes from /api/public-config; these tests use the shipped defaults (the same text) directly.
vi.mock("../agent/uiCopy", async () => {
  const { uiText } = await import("@naadi/shared");
  return {
    useUiCopy:
      () =>
      (key: string, vars: Record<string, string> = {}) =>
        uiText(null, key, vars),
  };
});
vi.mock("./InkLab", () => ({ InkLab: () => <h1>Ink lab (loaded)</h1> }));

/** Shape of GET /api/public-config as served by the API (values neutral; nothing secret in a fixture). */
function config(inkLab: boolean) {
  return {
    auth_configured: true,
    supabase_url: "https://project.supabase.test",
    supabase_anon_key: "public-anon-key",
    google_oauth_enabled: false,
    app_base_url: null,
    app_version: "test",
    features: { ink_lab: inkLab },
    nav: [],
    exam_rules: {
      block: { min_questions: 1, max_questions: 40, default_questions: 40 },
      diagnostic_questions: 40,
      exams: { step1: { label: "Step 1", blocks: 7, questions_per_block: 40, minutes_per_block: 60 } },
      modes: {
        tutor: { timed: false, seconds_per_question: null, explanations: "after_each_question", assistant: true },
        timed: { timed: true, seconds_per_question: 90, explanations: "after_block", assistant: true },
        exam: { timed: true, seconds_per_question: 90, explanations: "after_block", assistant: false },
      },
    },
    ui_copy: {},
  };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-ink-gate-1" },
  });
}

function renderAt(path: string) {
  const root = createRootRoute({ component: Outlet });
  const system = createRoute({ getParentRoute: () => root, path: "/system", component: () => <p>System</p> });
  const router = createRouter({
    routeTree: root.addChildren([system, inkLabRoute(root)]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  client.setConfig({ baseUrl: "http://naadi.test" });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ink lab gate", () => {
  it("shows a busy state while auth starts", async () => {
    auth.state = { status: "loading" };
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("status")).toHaveTextContent("Opening the ink lab…");
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  });

  it("signed out: follows the public flag (production says no)", async () => {
    auth.state = { status: "signed-out", config: config(false) };
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the design system" })).toHaveAttribute("href", "/system");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("signed out outside production: opens the lab", async () => {
    auth.state = { status: "signed-out", config: config(true) };
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("heading", { name: "Ink lab (loaded)" })).toBeInTheDocument();
  });

  it("signed in: /api/me decides per user (admin yes, student no)", async () => {
    auth.state = { status: "signed-in", config: config(false) };
    me.result = { data: { features: { ink_lab: true } }, isError: false, isFetching: false };
    const admin = renderAt("/system/ink-lab");
    expect(await screen.findByRole("heading", { name: "Ink lab (loaded)" })).toBeInTheDocument();
    admin.unmount();
    me.result = { data: { features: { ink_lab: false } }, isError: false, isFetching: false };
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("signed in: /api/me failing shows the error with its request id and retries", async () => {
    auth.state = { status: "signed-in", config: config(true) };
    const refetch = vi.fn();
    me.result = {
      data: undefined,
      isError: true,
      isFetching: false,
      error: new ApiError("Your profile cannot be loaded right now.", 503, "unavailable", "req-me-1"),
      refetch,
    };
    renderAt("/system/ink-lab");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The ink lab could not be opened");
    expect(alert).toHaveTextContent("Request ID req-me-1");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("auth unavailable: falls back to /api/public-config, with error and retry", async () => {
    auth.state = { status: "unavailable" };
    fetchMock.mockResolvedValueOnce(json(500, { error: { code: "internal_error", message: "x", request_id: "r" } }));
    fetchMock.mockResolvedValueOnce(json(200, config(false)));
    renderAt("/system/ink-lab");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The app configuration could not be loaded (HTTP 500).");
    expect(alert).toHaveTextContent("Request ID req-ink-gate-1");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    const [req] = fetchMock.mock.calls[0] as [Request];
    expect(new URL(req.url).pathname).toBe("/api/public-config");
  });

  it("auth unavailable: rejects a public config that breaks the contract", async () => {
    auth.state = { status: "unavailable" };
    fetchMock.mockResolvedValue(json(200, { features: {} }));
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("alert")).toHaveTextContent("does not understand");
  });
});
