from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from naadi_api.security import (
    CSP_REPORT_PATH,
    SEARCH_MEDIA_HOSTS,
    YOUTUBE_IMG_HOSTS,
    content_security_policy,
    trusted_types_report_only_policy,
    youtube_sandbox_content_security_policy,
)

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
    assert d["img-src"] == ["'self'", "data:", "blob:", *SEARCH_MEDIA_HOSTS, *YOUTUBE_IMG_HOSTS, SUPA]
    assert d["frame-src"] == ["'self'"]  # YouTube is framed by the sandboxed player page, not by the app
    assert d["worker-src"] == ["'self'"]
    assert d["base-uri"] == ["'self'"]
    assert d["report-uri"] == [CSP_REPORT_PATH]
    assert d["report-to"] == ["csp"]
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


def test_trusted_types_ship_report_only() -> None:
    d = _directives(trusted_types_report_only_policy())
    assert d["require-trusted-types-for"] == ["'script'"]
    assert d["report-uri"] == [CSP_REPORT_PATH]
    # Report-only must never carry the enforcing policy's directives (they would only be reported, not applied).
    assert set(d) == {"require-trusted-types-for", "report-uri", "report-to"}


def test_report_only_and_reporting_endpoint_headers(client_factory: Callable[..., TestClient]) -> None:
    h = client_factory(settings=make_settings(supabase_url=SUPA)).get("/api/health").headers
    assert h["content-security-policy-report-only"] == trusted_types_report_only_policy()
    assert h["reporting-endpoints"] == f'csp="{CSP_REPORT_PATH}"'
    # The enforcing policy is unchanged by the report-only rollout.
    assert "require-trusted-types-for" not in h["content-security-policy"]


def test_the_app_csp_has_no_third_party_script_or_frame() -> None:
    """The official YouTube player is third-party script: it runs only in the sandboxed player page, never here."""
    for settings in (make_settings(supabase_url=SUPA), make_settings(app_env="local")):
        d = _directives(content_security_policy(settings))
        assert d["script-src"] == ["'self'"]
        assert d["frame-src"] == ["'self'"]
        assert not any("youtube" in v for vals in d.values() for v in vals)


def test_the_youtube_player_page_has_its_own_opaque_origin_policy() -> None:
    d = _directives(youtube_sandbox_content_security_policy())
    assert d["script-src"] == ["'self'", "https://www.youtube.com"]
    assert d["frame-src"] == ["https://www.youtube-nocookie.com"]
    assert d["default-src"] == ["'none'"] and d["connect-src"] == ["'none'"]
    assert d["sandbox"] == ["allow-scripts"]  # never allow-same-origin
    assert d["frame-ancestors"] == ["'self'"]


def test_each_sandbox_document_is_served_with_its_own_policy(static_dir: Path) -> None:
    (static_dir / "sandbox").mkdir()
    for name in ("mermaid", "youtube"):
        (static_dir / "sandbox" / f"{name}.html").write_text("<!doctype html><title>x</title>")
    from naadi_api.main import create_app
    from tests.conftest import fake_service

    app = create_app(make_settings(static_dir=static_dir), health=fake_service())
    c = TestClient(app)
    yt = c.get("/sandbox/youtube.html").headers["content-security-policy"]
    mm = c.get("/sandbox/mermaid.html").headers["content-security-policy"]
    assert "https://www.youtube.com" in yt and "https://www.youtube.com" not in mm
    assert "sandbox allow-scripts" in yt


def test_sandbox_documents_skip_the_trusted_types_rollout(
    client_factory: Callable[..., TestClient], static_dir: Path
) -> None:
    """The opaque-origin sandbox pages exist to run their own scripts (Mermaid, the YouTube API): they keep their own
    strict CSP and do not get the app's Trusted Types report-only policy; every app page does."""
    (static_dir / "sandbox").mkdir()
    for name in ("mermaid.html", "youtube.html"):
        (static_dir / "sandbox" / name).write_text("<!doctype html><title>x</title>")
    c = client_factory()
    for name in ("mermaid.html", "youtube.html"):
        h = c.get(f"/sandbox/{name}").headers
        assert "sandbox" in h["content-security-policy"] or "frame-src" in h["content-security-policy"], name
        assert "content-security-policy-report-only" not in h, name
    for path in ("/", "/learn", "/api/health"):
        assert "require-trusted-types-for" in c.get(path).headers["content-security-policy-report-only"], path
