import { client } from "@naadi/shared";
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
  configured: true,
  models: null,
};

const cardsOf = () => Array.from(document.querySelectorAll<HTMLElement>("[data-component]"));

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
  // Node's Request needs an absolute URL; in the browser the client uses same-origin relative paths.
  client.setConfig({ baseUrl: "http://naadi.test" });
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
    await screen.findByRole("heading", { name: "Database" });
    const cards = cardsOf();
    expect(cards).toHaveLength(5);
    for (const name of ["Database", "Storage", "Tavily", "OpenRouter", "NVIDIA"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("Overall status:")).toBeInTheDocument();
    const db = within(cards[0] as HTMLElement);
    expect(db.getByText("OK")).toBeInTheDocument();
    expect(db.getByText("select 1 succeeded")).toBeInTheDocument();
    const para = (re: RegExp) => (_: string, el: Element | null) =>
      el?.tagName === "P" && re.test(el.textContent ?? "");
    expect(screen.getByText(para(/^12 of 50 requests used today$/))).toBeInTheDocument();
    expect(screen.getByText(para(/^2 of 950 credits used this month \(provider limit 1000\)$/))).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const sent = fetchMock.mock.calls[0]?.[0] as Request;
    const url = new URL(sent.url);
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
    await screen.findByRole("heading", { name: "NVIDIA" });
    expect(cardsOf()).toHaveLength(5);
  });

  it("lists each NVIDIA model probe with a word, not only a colour", async () => {
    const report = structuredClone(healthy);
    const nvidia = (report.components as Record<string, Json>).nvidia as Json;
    const models = nvidia.models as Json[];
    (models[1] as Json).ok = false;
    (models[1] as Json).detail = "HTTP 410 (retired)";
    nvidia.status = "degraded";
    nvidia.detail = "3 of 4 configured models answer. Failing: vendor-b/chat-model-2 HTTP 410 (retired)";
    fetchMock.mockResolvedValue(jsonResponse(200, report));
    renderPanel();
    const card = within((await screen.findByRole("heading", { name: "NVIDIA" })).closest("li") as HTMLElement);
    const list = within(card.getByRole("list", { name: "Configured models" }));
    expect(list.getAllByRole("listitem")).toHaveLength(4);
    expect(list.getByText("vendor-b/chat-model-2").closest("li")).toHaveTextContent("Fails: HTTP 410 (retired)");
    expect(list.getByText("vendor-a/chat-model-1").closest("li")).toHaveTextContent(/Answers · \d+ ms/);
    expect(card.getByText("Degraded")).toBeInTheDocument();
  });

  it("shows an unconfigured NVIDIA key as degraded with the variable name", async () => {
    const report = structuredClone(healthy);
    (report.components as Record<string, Json>).nvidia = {
      status: "degraded",
      latency_ms: null,
      detail: "Not configured: NVIDIA_API_KEY is not set on this deployment",
      checked_at: "2026-11-16T05:51:11.430770Z",
      cached: false,
      quota: null,
      free_tier: null,
      configured: false,
      models: null,
    };
    report.status = "degraded";
    fetchMock.mockResolvedValue(jsonResponse(200, report));
    renderPanel();
    const card = within((await screen.findByRole("heading", { name: "NVIDIA" })).closest("li") as HTMLElement);
    expect(card.getByText("Degraded")).toBeInTheDocument();
    expect(card.getByText(/NVIDIA_API_KEY is not set/)).toBeInTheDocument();
    expect(card.getByText("n/a")).toBeInTheDocument();
    expect(card.queryByRole("list", { name: "Configured models" })).not.toBeInTheDocument();
  });

  it("shows generation as not configured (not an outage) until the token is set", async () => {
    const report = structuredClone(healthy);
    (report.components as Record<string, Json>).generation = {
      status: "ok",
      latency_ms: null,
      detail: "Not configured: HF_GEN_TOKEN",
      checked_at: "2026-11-16T05:51:11.430770Z",
      cached: false,
      configured: false,
    };
    fetchMock.mockResolvedValue(jsonResponse(200, report));
    renderPanel();
    const card = within((await screen.findByRole("heading", { name: "Generation" })).closest("li") as HTMLElement);
    expect(card.getByText("Not configured")).toBeInTheDocument();
    expect(card.getByText(/HF_GEN_TOKEN/)).toBeInTheDocument();
  });

  it("shows a generation quota pause with its reset time", async () => {
    const report = structuredClone(healthy);
    (report.components as Record<string, Json>).generation = {
      status: "degraded",
      latency_ms: 4,
      detail: "ZeroGPU quota paused",
      checked_at: "2026-11-16T05:51:11.430770Z",
      cached: false,
      configured: true,
      generation: { state: "paused", resets_at: "2026-11-17T00:00:00Z" },
    };
    fetchMock.mockResolvedValue(jsonResponse(200, report));
    renderPanel();
    const card = within((await screen.findByRole("heading", { name: "Generation" })).closest("li") as HTMLElement);
    expect(card.getByText("Degraded")).toBeInTheDocument();
    expect(card.getByText(/Quota paused until/)).toBeInTheDocument();
  });

  it("labels the last result stale when a refetch fails, with the request id and a retry", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, healthy));
    renderPanel();
    await screen.findByRole("heading", { name: "Database" });
    expect(screen.getByText("Overall status:").parentElement).toHaveTextContent("OK");
    fetchMock.mockResolvedValueOnce(
      jsonResponse(500, {
        error: { code: "internal_error", message: "Something went wrong on our side.", request_id: "req-stale-500" },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Check again/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The latest check failed");
    expect(alert).toHaveTextContent("Something went wrong on our side. (HTTP 500)");
    expect(alert).toHaveTextContent(/last successful check at .+ and may be out of date/);
    expect(alert).toHaveTextContent("Request ID req-stale-500");
    expect(screen.getByText("Overall status:").parentElement).toHaveTextContent("Stale");
    expect(screen.queryByText("Overall status:")?.parentElement).not.toHaveTextContent(/^Overall status:\s*OK$/);
    expect(cardsOf()).toHaveLength(5);
    expect(document.querySelector("[data-stale]")).toHaveAttribute("data-stale", "true");
    expect(screen.getByText(/^Stale · Version/)).toBeInTheDocument();

    fetchMock.mockResolvedValueOnce(jsonResponse(200, healthy));
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("Overall status:").parentElement).toHaveTextContent("OK");
    expect(document.querySelector("[data-stale]")).toHaveAttribute("data-stale", "false");
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
    await screen.findByRole("heading", { name: "Database" });
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    const banner = await screen.findByText("Offline");
    expect(banner.closest('[role="status"]')).toHaveTextContent("Showing the last result from");
    expect(cardsOf()).toHaveLength(5);
    expect(screen.getByRole("button", { name: /Check again/ })).toBeDisabled();
  });
});
