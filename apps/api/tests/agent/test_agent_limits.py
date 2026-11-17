"""Per-student limits on Aegra's protocol routes (naadi_api.agent.limits, segment 22)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from naadi_api.agent.limits import AgentRateLimit


def _app(**kw: int) -> TestClient:
    app = FastAPI()

    @app.post("/threads/{tid}/runs/stream")
    async def stream(tid: str) -> dict[str, str]:
        return {"ok": tid}

    @app.get("/threads/{tid}")
    async def thread(tid: str) -> dict[str, str]:
        return {"ok": tid}

    @app.get("/api/me")
    async def me() -> dict[str, str]:
        return {"ok": "me"}

    app.add_middleware(AgentRateLimit, **kw)
    return TestClient(app)


def test_runs_are_limited_per_credential() -> None:
    c = _app(runs_per_min=3)
    a, b = {"authorization": "Bearer token-a"}, {"authorization": "Bearer token-b"}
    codes = [c.post("/threads/t1/runs/stream", headers=a).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.post("/threads/t1/runs/stream", headers=a)
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
    assert c.post("/threads/t1/runs/stream", headers=b).status_code == 200  # another student is unaffected
    assert c.get("/threads/t1", headers=a).status_code == 200  # reads are not runs


def test_all_protocol_requests_have_a_ceiling_and_api_routes_are_untouched() -> None:
    c = _app(requests_per_min=5)
    a = {"authorization": "Bearer token-a"}
    assert [c.get("/threads/t", headers=a).status_code for _ in range(6)][-1] == 429
    assert all(c.get("/api/me", headers=a).status_code == 200 for _ in range(10))
    # Without a credential Aegra's own auth answers (401 there); this layer passes it through.
    assert all(c.get("/threads/t").status_code == 200 for _ in range(10))
