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
import { inkLabRoute } from "./route";

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
  it("shows a busy state while the server flag loads", async () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("status")).toHaveTextContent("Opening the ink lab…");
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  });

  it("is not found when the server turns the flag off (production)", async () => {
    fetchMock.mockResolvedValue(json(200, config(false)));
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the design system" })).toHaveAttribute("href", "/system");
    const [req] = fetchMock.mock.calls[0] as [Request];
    expect(new URL(req.url).pathname).toBe("/api/public-config");
  });

  it("shows the error with its request id and retries", async () => {
    fetchMock.mockResolvedValueOnce(json(500, { error: { code: "internal_error", message: "x", request_id: "r" } }));
    fetchMock.mockResolvedValueOnce(json(200, config(false)));
    renderAt("/system/ink-lab");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The ink lab could not be opened");
    expect(alert).toHaveTextContent("The app configuration could not be loaded (HTTP 500).");
    expect(alert).toHaveTextContent("Request ID req-ink-gate-1");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("rejects a response that breaks the contract", async () => {
    fetchMock.mockResolvedValue(json(200, { features: {} }));
    renderAt("/system/ink-lab");
    expect(await screen.findByRole("alert")).toHaveTextContent("does not understand");
  });
});
