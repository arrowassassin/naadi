"""Admin SearXNG probe (segment 22): structured outcome, admin-only, never enables the layer."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient

from naadi_api.auth import CurrentUser, get_account_user

BODY = {
    "results": [
        {"url": "https://www.ncbi.nlm.nih.gov/x", "title": "MI", "content": "<b>troponin</b>", "engines": ["brave"]},
        {"url": "http://insecure.example/y", "title": "skip", "engines": ["mojeek"]},
        {"url": "https://en.wikipedia.org/wiki/MI", "title": "MI", "engines": ["wikipedia", "duckduckgo"]},
    ],
    "unresponsive_engines": [["mojeek", "timeout"]],
}


def _client(client_factory: Callable[..., TestClient], *, admin: bool) -> TestClient:
    c = client_factory()
    user = CurrentUser(user_id=uuid.uuid4(), email="a@example.com", email_verified=True, is_admin=admin)
    c.app.dependency_overrides[get_account_user] = lambda: user  # type: ignore[attr-defined]
    return c


def test_probe_reports_parsed_results_and_engines(client_factory: Callable[..., TestClient]) -> None:
    c = _client(client_factory, admin=True)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("http://127.0.0.1:8888/search").respond(200, json=BODY)
        r = c.get("/api/admin/search/searxng-probe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "ok" and body["results"] == 2  # the http:// result is dropped by the parser
    assert body["engines"] == ["brave", "duckduckgo", "mojeek", "wikipedia"]
    assert body["unresponsive"] == ["mojeek"]
    assert body["layer_enabled"] is False  # shipped off; the probe never changes that
    assert route.calls.last.request.url.params["format"] == "json"


def test_probe_outcomes_when_searxng_is_down_or_odd(client_factory: Callable[..., TestClient]) -> None:
    c = _client(client_factory, admin=True)
    with respx.mock() as mock:
        mock.get("http://127.0.0.1:8888/search").mock(side_effect=httpx.ConnectError("refused"))
        assert c.get("/api/admin/search/searxng-probe").json()["outcome"] == "unreachable"
        mock.get("http://127.0.0.1:8888/search").respond(429)
        out = c.get("/api/admin/search/searxng-probe").json()
        assert (out["outcome"], out["http_status"]) == ("http_error", 429)
        mock.get("http://127.0.0.1:8888/search").respond(200, json={"results": []})
        assert c.get("/api/admin/search/searxng-probe").json()["outcome"] == "no_results"


def test_probe_is_admin_only(client_factory: Callable[..., TestClient]) -> None:
    c = _client(client_factory, admin=False)
    assert c.get("/api/admin/search/searxng-probe").status_code == 403
