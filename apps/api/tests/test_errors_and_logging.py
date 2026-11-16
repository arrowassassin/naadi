from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from .conftest import LogCapture, make_settings


class In(BaseModel):
    n: int


def _boom_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom with postgresql://u:topsecretpw@h:5432/db")

    @r.post("/api/echo")
    async def echo(body: In) -> dict[str, int]:
        return {"n": body.n}

    return r


def _with_router(client: TestClient) -> TestClient:
    app = client.app
    router = _boom_router()
    # Insert ahead of the SPA catch-all routes.
    app.router.routes[0:0] = router.routes  # type: ignore[attr-defined]
    return client


def test_unknown_api_path_is_json_404(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    for method in ("GET", "POST", "DELETE"):
        r = c.request(method, "/api/does-not-exist")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["request_id"] == r.headers["x-request-id"]


def test_wrong_method_on_known_api_path_is_405_with_allow(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    for method in ("POST", "PUT", "DELETE"):
        r = c.request(method, "/api/health")
        assert r.status_code == 405, method
        assert r.json()["error"]["code"] == "method_not_allowed"
        assert r.headers["allow"] == "GET"
        assert r.json()["error"]["request_id"] == r.headers["x-request-id"]
    assert c.post("/api/openapi.json").headers["allow"] == "GET, HEAD"


def test_validation_error_shape_never_echoes_input(client_factory: Callable[..., TestClient]) -> None:
    c = _with_router(client_factory())
    r = c.post("/api/echo", json={"n": "my private note text"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"][0]["loc"] == ["body", "n"]
    assert "my private note text" not in r.text


def test_unhandled_exception_prod_has_no_trace(client_factory: Callable[..., TestClient], logs: LogCapture) -> None:
    c = _with_router(client_factory())
    r = c.get("/api/boom", headers={"X-Request-ID": "req-boom-0001"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err == {
        "code": "internal_error",
        "message": "Something went wrong on our side. Try again in a moment.",
        "request_id": "req-boom-0001",
    }
    assert "Traceback" not in r.text and "kaboom" not in r.text
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    entries = [e for e in logs.lines() if e.get("event") == "unhandled_exception"]
    assert len(entries) == 1
    assert entries[0]["request_id"] == "req-boom-0001"
    assert "RuntimeError" in entries[0]["exception"]
    assert "Traceback" in entries[0]["exception"]
    assert "topsecretpw" not in logs.text


def test_registered_secret_is_redacted_from_every_log_path(
    client_factory: Callable[..., TestClient], logs: LogCapture, static_dir: object
) -> None:
    """A secret with no recognisable shape (so only the registered value can catch it) raised from a route
    and logged through a plain stdlib logger, with and without a traceback."""
    import logging

    secret = "plain-db-password-9f8e7d"
    settings = make_settings(static_dir=static_dir, database_url=f"postgresql://u:{secret}@h:5432/db")
    c = client_factory(settings=settings)
    r = APIRouter()

    @r.get("/api/leak")
    async def leak() -> None:
        raise RuntimeError(f"auth failed, password was {secret}")

    c.app.router.routes[0:0] = r.routes  # type: ignore[attr-defined]
    assert c.get("/api/leak").status_code == 500
    try:
        raise ValueError(f"third-party said {secret}")
    except ValueError:
        logging.getLogger("psycopg.pool").exception("pool error %s", secret)
    assert secret not in logs.text
    assert logs.text.count("***") >= 3


def test_unhandled_exception_local_shows_scrubbed_message(
    client_factory: Callable[..., TestClient], static_dir: object
) -> None:
    c = _with_router(client_factory(settings=make_settings(app_env="local", static_dir=static_dir)))
    r = c.get("/api/boom")
    assert r.status_code == 500
    msg = r.json()["error"]["message"]
    assert msg.startswith("RuntimeError: kaboom")
    assert "topsecretpw" not in msg
    assert "Traceback" not in r.text


def test_request_id_generated_and_propagated(client_factory: Callable[..., TestClient], logs: LogCapture) -> None:
    c = client_factory()
    r1 = c.get("/api/health")
    generated = r1.headers["x-request-id"]
    assert len(generated) == 32
    r2 = c.get("/api/health", headers={"X-Request-ID": "trace-abc-123456"})
    assert r2.headers["x-request-id"] == "trace-abc-123456"
    r3 = c.get("/api/health", headers={"X-Request-ID": "bad id; with spaces"})
    assert r3.headers["x-request-id"] != "bad id; with spaces"
    reqs = [e for e in logs.lines() if e.get("event") == "request"]
    assert [e["request_id"] for e in reqs] == [generated, "trace-abc-123456", r3.headers["x-request-id"]]
    for e in reqs:
        assert set(e) >= {"request_id", "method", "path", "status", "duration_ms", "ts", "level"}


def test_access_log_has_no_body_query_or_auth(client_factory: Callable[..., TestClient], logs: LogCapture) -> None:
    c = _with_router(client_factory())
    c.post(
        "/api/echo?access_token=qs-token-SECRET",
        json={"n": 3, "note": "BODY-SECRET-NOTE"},
        headers={"Authorization": "Bearer AUTH-SECRET-TOKEN", "Cookie": "sb=COOKIE-SECRET"},
    )
    text = logs.text
    for needle in ("qs-token-SECRET", "BODY-SECRET-NOTE", "AUTH-SECRET-TOKEN", "COOKIE-SECRET", "access_token"):
        assert needle not in text
    line = next(e for e in logs.lines() if e.get("event") == "request")
    assert line["path"] == "/api/echo"
    assert line["status"] == 200


def test_logger_drops_forbidden_keys(logs: LogCapture) -> None:
    from naadi_api.logs import get_logger

    get_logger("t").info("x", body="b0dy", authorization="auth", query="q", ok=1)
    e = logs.lines()[-1]
    assert e["ok"] == 1
    assert not {"body", "authorization", "query"} & set(e)
