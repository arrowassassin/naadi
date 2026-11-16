from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from naadi_api.security import SEARCH_MEDIA_HOSTS, content_security_policy

from .conftest import make_settings

SUPA = "https://abcdefghijklmnopqrst.supabase.co"


def _directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        bits = part.strip().split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def test_csp_is_strict() -> None:
    d = _directives(content_security_policy(make_settings(supabase_url=SUPA)))
    assert d["default-src"] == ["'self'"]
    assert d["script-src"] == ["'self'"]
    assert d["style-src"] == ["'self'"]
    assert d["font-src"] == ["'self'"]
    assert d["connect-src"] == ["'self'", SUPA, "wss://abcdefghijklmnopqrst.supabase.co"]
    assert d["img-src"] == ["'self'", "data:", "blob:", *SEARCH_MEDIA_HOSTS, SUPA]
    assert d["frame-ancestors"] == ["'self'", "https://huggingface.co"]
    assert d["object-src"] == ["'none'"]
    assert "upgrade-insecure-requests" in d
    joined = " ".join(v for vals in d.values() for v in vals)
    assert "unsafe-inline" not in joined and "unsafe-eval" not in joined and "*" not in joined


def test_local_csp_does_not_upgrade_http() -> None:
    d = _directives(content_security_policy(make_settings(app_env="local")))
    assert "upgrade-insecure-requests" not in d
    assert d["connect-src"] == ["'self'"]


def test_headers_on_api_static_spa_and_errors(client_factory: Callable[..., TestClient], static_dir: Path) -> None:
    c = client_factory(settings=make_settings(static_dir=static_dir, supabase_url=SUPA))
    for path in ("/api/health", "/", "/system", "/assets/index-abc123.js", "/api/nope", "/assets/missing.js"):
        r = c.get(path)
        h = r.headers
        assert "frame-ancestors 'self' https://huggingface.co" in h["content-security-policy"], path
        assert h["strict-transport-security"].startswith("max-age=63072000"), path
        assert h["x-content-type-options"] == "nosniff", path
        assert h["referrer-policy"] == "strict-origin-when-cross-origin", path
        assert "camera=()" in h["permissions-policy"], path
        assert "x-frame-options" not in h, path  # frame-ancestors governs framing (HF iframe)
        assert h["x-request-id"], path


def test_api_responses_are_not_cached(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    assert c.get("/api/health").headers["cache-control"] == "no-store"
    assert c.get("/api/nope").headers["cache-control"] == "no-store"
