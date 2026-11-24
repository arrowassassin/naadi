"""The diagram sandbox document and the app CSP (S06 review item 2 and 11)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from naadi_api.security import content_security_policy, sandbox_content_security_policy
from tests.conftest import make_settings


def _directives(csp: str) -> dict[str, str]:
    return {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in (x.strip() for x in csp.split(";"))}


def test_app_csp_frames_only_self() -> None:
    d = _directives(content_security_policy(make_settings()))
    # YouTube is framed by the player page on its own origin, never by the app document.
    assert d["frame-src"] == "'self' https://arrowassassin-naadi-player.static.hf.space"
    assert d["style-src"] == "'self'"  # the app itself never allows inline styles
    assert d["media-src"] == "'self' blob: https://upload.wikimedia.org"  # Commons video files, no Supabase here


def test_sandbox_csp_allows_inline_styles_only_there_and_stays_sandboxed() -> None:
    d = _directives(sandbox_content_security_policy())
    assert d["default-src"] == "'none'"
    assert d["script-src"] == "'self'"
    assert d["style-src"] == "'self' 'unsafe-inline'"
    assert d["sandbox"] == "allow-scripts"
    assert d["frame-ancestors"] == "'self'"
    assert "connect-src" not in d  # falls back to default-src 'none': the renderer cannot call anything


def test_sandbox_document_gets_its_own_csp_and_assets_allow_cors(
    client_factory: Callable[..., TestClient], static_dir: Path
) -> None:
    (static_dir / "sandbox").mkdir()
    (static_dir / "sandbox" / "mermaid.html").write_text("<!doctype html><title>x</title>")
    c = client_factory()
    r = c.get("/sandbox/mermaid.html")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == sandbox_content_security_policy()
    page = c.get("/")
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    asset = c.get("/assets/index-abc123.js")
    assert asset.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-origin" not in page.headers
