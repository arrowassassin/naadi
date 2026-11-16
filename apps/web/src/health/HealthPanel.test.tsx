import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import recorded from "./__fixtures__/health-sandbox-db-down.json";
import { HealthPanel } from "./HealthPanel";

type Json = Record<string, unknown>;

/** The recorded sandbox response (Postgres unreachable from the build sandbox) and a healthy variant. */
const dbDown: Json = structuredClone(recorded);
const healthy: Json = structuredClone(recorded);
healthy.status = "ok";
(healthy.components as Record<string, Json>).database = {
  status: "ok",
  latency_ms: 38.2,
  detail: "select 1 succeeded",
  checked_at: "2026-11-16T02:48:37.149803Z",
  cached: false,
  quota: null,
  free_tier: null,
};

function jsonResponse(status: number, body: unknown, requestId = "req-test-000001") {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": requestId },
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <HealthPanel />
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HealthPanel", () => {
  it("shows skeletons and a busy region while loading", async () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    const { container } = renderPanel();
    expect(screen.getByRole("status")).toHaveTextContent("Checking system health…");
    expect(container.querySelector("section")).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll("[data-skeleton]").length).toBeGreaterThan(4);
  });

  it("renders every component with a status word when healthy", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, healthy));
    renderPanel();
    const cards = await screen.findAllByRole("listitem");
    expect(cards).toHaveLength(4);
    for (const name of ["Database", "Storage", "OpenRouter", "Tavily"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("Overall status:")).toBeInTheDocument();
    const db = within(cards[0] as HTMLElement);
    expect(db.getByText("OK")).toBeInTheDocument();
    expect(db.getByText("select 1 succeeded")).toBeInTheDocument();
    expect(screen.getByText(/0 of 50 requests used today/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 950 credits used this month \(provider limit 1000\)/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const url = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(url.pathname).toBe("/api/health");
  });

  it("shows the 503 report with an alert when the database is down", async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, dbDown, "req-503-abcdef"));
    renderPanel();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The database is down");
    expect(alert).toHaveTextContent("req-503-abcdef");
    const dbCard = document.querySelector('[data-component="database"]') as HTMLElement;
    expect(within(dbCard).getByText("Down")).toBeInTheDocument();
    expect(within(dbCard).getByText(/PoolTimeout/)).toBeInTheDocument();
  });

  it("shows an error with the request id and recovers on retry", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(500, {
        error: { code: "internal_error", message: "Something went wrong on our side.", request_id: "req-500-xyz123" },
      }),
    );
    renderPanel();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("System health could not be loaded");
    expect(alert).toHaveTextContent("Something went wrong on our side. (HTTP 500)");
    expect(alert).toHaveTextContent("req-500-xyz123");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, healthy));
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(await screen.findAllByRole("listitem")).toHaveLength(4);
  });

  it("explains a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("The API could not be reached");
  });

  it("rejects a response that does not match the contract", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ...healthy, status: "fine" }));
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("does not understand");
  });

  it("shows the offline banner and keeps the last result", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, healthy));
    renderPanel();
    await screen.findAllByRole("listitem");
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    const banner = await screen.findByText("Offline");
    expect(banner.closest('[role="status"]')).toHaveTextContent("Showing the last result from");
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /Check again/ })).toBeDisabled();
  });
});
