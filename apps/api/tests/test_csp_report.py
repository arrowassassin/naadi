"""`POST /api/csp-report` (segment 22): summaries only, never bodies; per-IP limit; always 204."""

from __future__ import annotations

import io
import json
from collections.abc import Callable

from fastapi.testclient import TestClient

from naadi_api.csp_report import _Dedup, summarize
from naadi_api.logs import configure_logging

SECRET_SAMPLE = "patient Ramesh 9876543210 note text"


def _legacy(**over: str) -> dict[str, object]:
    body = {
        "document-uri": "https://arrowassassin-naadi.hf.space/learn/cardio?token=abc#frag",
        "violated-directive": "style-src-attr",
        "effective-directive": "style-src-attr",
        "blocked-uri": "inline",
        "disposition": "enforce",
        "script-sample": SECRET_SAMPLE,
    }
    body.update(over)
    return {"csp-report": body}


def _reporting_api() -> list[dict[str, object]]:
    return [
        {
            "type": "csp-violation",
            "url": "https://x/notes/1?t=secret",
            "body": {
                "documentURL": "https://x/notes/1?t=secret",
                "effectiveDirective": "require-trusted-types-for",
                "blockedURL": "trusted-types-sink",
                "disposition": "report",
                "sample": SECRET_SAMPLE,
            },
        },
        {"type": "deprecation", "body": {"id": "x"}},
    ]


def test_summaries_drop_bodies_queries_and_paths() -> None:
    assert summarize(_legacy()) == [
        {"directive": "style-src-attr", "disposition": "enforce", "blocked": "inline", "document": "/learn/cardio"}
    ]
    assert summarize(_legacy(**{"blocked-uri": "https://evil.example.com/x.js?k=1"}))[0]["blocked"] == (
        "https://evil.example.com"
    )
    assert summarize(_legacy(**{"blocked-uri": "data:text/html;base64,AAAA"}))[0]["blocked"] == "data"
    assert summarize(_reporting_api()) == [
        {
            "directive": "require-trusted-types-for",
            "disposition": "report",
            "blocked": "trusted-types-sink",
            "document": "/notes/1",
        }
    ]
    assert summarize({"nope": 1}) == [] and summarize("x") == [] and summarize(None) == []


def test_endpoint_logs_summary_never_body(client_factory: Callable[..., TestClient]) -> None:
    buf = io.StringIO()
    c = client_factory()
    configure_logging("INFO", stream=buf)
    r = c.post("/api/csp-report", content=json.dumps(_legacy()), headers={"content-type": "application/csp-report"})
    assert r.status_code == 204 and r.content == b""
    r = c.post(
        "/api/csp-report", content=json.dumps(_reporting_api()), headers={"content-type": "application/reports+json"}
    )
    assert r.status_code == 204
    out = buf.getvalue()
    assert '"csp_violation"' in out and "style-src-attr" in out and "require-trusted-types-for" in out
    assert SECRET_SAMPLE not in out and "token=abc" not in out and "secret" not in out


def test_endpoint_tolerates_junk_and_caps_size(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    assert c.post("/api/csp-report", content=b"{not json").status_code == 204
    assert c.post("/api/csp-report", content=b"x" * (17 * 1024)).status_code == 204
    assert c.post("/api/csp-report").status_code == 204


def test_endpoint_is_rate_limited_per_client(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    a = {"x-forwarded-for": "203.0.113.7"}
    codes = [c.post("/api/csp-report", content=b"{}", headers=a).status_code for _ in range(31)]
    assert codes[:30] == [204] * 30 and codes[30] == 429
    # Another client is unaffected.
    assert c.post("/api/csp-report", content=b"{}", headers={"x-forwarded-for": "203.0.113.8"}).status_code == 204


def test_dedup_logs_once_per_minute_with_count() -> None:
    now = [0.0]
    d = _Dedup(clock=lambda: now[0])
    s = {"directive": "img-src", "disposition": "enforce", "blocked": "https://a", "document": "/"}
    assert d.offer(s) == 1
    assert d.offer(s) is None and d.offer(s) is None
    now[0] = 61.0
    assert d.offer(s) == 3  # the first one plus the two suppressed
