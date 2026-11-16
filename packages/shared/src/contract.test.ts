import { afterEach, describe, expect, it, vi } from "vitest";
import { client, getHealth, zErrorResponse, zHealthResponse } from "./index";

const component = {
  status: "ok",
  latency_ms: 12.5,
  detail: "select 1 succeeded",
  checked_at: "2026-11-16T02:48:37.149905Z",
  cached: false,
  quota: null,
  free_tier: null,
};

const healthy = {
  status: "ok",
  version: "abc1234",
  environment: "prod",
  checked_at: "2026-11-16T02:48:37.150000Z",
  components: { database: component, storage: component, openrouter: component, tavily: component },
};

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn(
    async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
  client.setConfig({ fetch: fetchMock as unknown as typeof fetch, baseUrl: "http://api.test" });
  return fetchMock;
}

afterEach(() => {
  client.setConfig({ fetch: globalThis.fetch, baseUrl: "" });
});

describe("generated client", () => {
  it("calls /api/health and returns the validated body", async () => {
    const fetchMock = respond(200, healthy);
    const res = await getHealth();
    expect(res.error).toBeUndefined();
    expect(res.data?.components.database.status).toBe("ok");
    const req = fetchMock.mock.calls[0] as unknown as [Request];
    expect(new URL(req[0].url).pathname).toBe("/api/health");
  });

  it("rejects a response that drifts from the contract", async () => {
    respond(200, { ...healthy, status: "fine" });
    const res = await getHealth();
    expect(res.data).toBeUndefined();
    expect((res.error as unknown as Error).name).toBe("ZodError");
  });

  it("rejects a response missing a component", async () => {
    const { tavily: _omit, ...rest } = healthy.components;
    respond(200, { ...healthy, components: rest });
    const res = await getHealth();
    expect(res.data).toBeUndefined();
    expect((res.error as unknown as Error).name).toBe("ZodError");
  });

  it("surfaces a 503 body as the error so callers can parse it", async () => {
    const down = {
      ...healthy,
      status: "down",
      components: { ...healthy.components, database: { ...component, status: "down" } },
    };
    respond(503, down);
    const res = await getHealth();
    expect(res.data).toBeUndefined();
    expect(res.response?.status).toBe(503);
    expect(zHealthResponse.parse(res.error).components.database.status).toBe("down");
  });

  it("error envelope schema matches the API", () => {
    expect(
      zErrorResponse.parse({ error: { code: "not_found", message: "Not Found", request_id: "abc" } }).error.code,
    ).toBe("not_found");
  });
});
