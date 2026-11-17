"""Per-client request floor (segment 22) and the client key it uses."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from naadi_api.auth_limits import CLIENT_FLOOR_PER_MIN, client_ip, counted_client


def _scope(xff: list[bytes] | None = None, peer: str = "10.0.0.5") -> dict[str, object]:
    headers = [(b"x-forwarded-for", v) for v in (xff or [])]
    return {"headers": headers, "client": (peer, 1234)}


def test_client_key_is_the_proxy_appended_entry_not_the_spoofable_first() -> None:
    # A caller can send any X-Forwarded-For; HF's proxy appends the peer it saw at the end.
    assert client_ip(_scope([b"1.2.3.4, 198.51.100.9"])) == "198.51.100.9"
    assert client_ip(_scope([b"1.2.3.4", b"198.51.100.9"])) == "198.51.100.9"
    assert client_ip(_scope()) == "10.0.0.5"
    assert client_ip({"headers": []}) == "unknown"


def test_direct_loopback_is_not_counted_but_forwarded_loopback_is() -> None:
    assert counted_client(_scope(peer="127.0.0.1")) is None
    assert counted_client(_scope(peer="::1")) is None
    assert counted_client(_scope([b"127.0.0.1"], peer="127.0.0.1")) == "127.0.0.1"
    assert counted_client(_scope(peer="10.0.0.5")) == "10.0.0.5"


def test_floor_returns_429_with_retry_after_and_headers(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    who = {"x-forwarded-for": "198.51.100.20"}
    # The SPA shell counts; hashed assets do not.
    for _ in range(CLIENT_FLOOR_PER_MIN):
        c.get("/assets/index-abc123.js", headers=who)
    statuses = [c.get("/system", headers=who).status_code for _ in range(CLIENT_FLOOR_PER_MIN)]
    assert set(statuses) == {200}
    r = c.get("/system", headers=who)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    assert "content-security-policy" in r.headers and r.headers["x-request-id"]
    assert c.get("/system", headers={"x-forwarded-for": "198.51.100.21"}).status_code == 200
