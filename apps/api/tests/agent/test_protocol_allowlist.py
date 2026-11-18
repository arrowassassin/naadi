"""Deny by default on Aegra's protocol routes (S14 review14): every route the host mounts is either one the web app
uses (PROTOCOL_ALLOWLIST) or refused by ProtocolAllowlist, and every run-creating route counts against the run
limit. A new Aegra release that mounts another route fails this test until it is reviewed."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from naadi_api.agent.auth import (
    PROTOCOL_ALLOWLIST,
    ProtocolAllowlist,
    is_protocol_path,
    protocol_route_allowed,
)
from naadi_api.agent.limits import _RUN_START

API_DIR = Path(__file__).resolve().parents[2]
NAADI_OWNED = ("/api/",)
SPA_FALLBACK = "/{full_path:path}"


_LIST_ROUTES = """
import contextlib, io, json, logging
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    from naadi_api.agent.host import app
out = set()
for r in app.routes:
    items = []
    if type(r).__name__ == "_IncludedRouter":
        ctxs = r.effective_route_contexts
        for c in (ctxs() if callable(ctxs) else ctxs):
            items.append((getattr(c, "path", None) or getattr(getattr(c, "route", c), "path", ""), c))
    else:
        items.append((getattr(r, "path", ""), r))
    for path, obj in items:
        rt = getattr(obj, "route", obj)
        for m in getattr(rt, "methods", None) or ["ANY"]:
            if m != "HEAD":
                out.add((m, path))
print("ROUTES:" + json.dumps(sorted(out)))
"""


def _mounted() -> set[tuple[str, str]]:
    """The host's mounted routes, listed in a subprocess: building the hosted app reconfigures logging and global
    runtime state, which must not leak into the rest of the unit run."""
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", _LIST_ROUTES], cwd=API_DIR, capture_output=True, text=True, timeout=120, check=True
    )
    line = next(x for x in proc.stdout.splitlines() if x.startswith("ROUTES:"))
    return {(m, p) for m, p in json.loads(line.removeprefix("ROUTES:"))}


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "abc-123", path)


def test_every_mounted_route_is_allowlisted_or_refused() -> None:
    mounted = _mounted()
    protocol = {(m, p) for m, p in mounted if not p.startswith(NAADI_OWNED) and p != SPA_FALLBACK}
    assert len(protocol) > 20, "the Aegra routes were found"
    unreviewed = [
        f"{m} {p}"
        for m, p in sorted(protocol)
        if not protocol_route_allowed(m, _concrete(p)) and not is_protocol_path(_concrete(p))
    ]
    assert unreviewed == [], "an Aegra route outside the protocol prefixes: allowlist or refuse it"
    allowed = sorted(f"{m} {p}" for m, p in protocol if protocol_route_allowed(m, _concrete(p)))
    assert allowed == sorted(f"{m} {p}" for m, p in PROTOCOL_ALLOWLIST), "the allowlist is exactly what is mounted"
    # The v2 event-streaming routes and every state or metadata write are refused.
    for m, p in [
        ("POST", "/threads/{thread_id}/commands"),
        ("POST", "/threads/{thread_id}/stream/events"),
        ("POST", "/threads/{thread_id}/state"),
        ("POST", "/threads/{thread_id}/state/checkpoint"),
        ("PATCH", "/threads/{thread_id}"),
        ("DELETE", "/threads/{thread_id}"),
        ("POST", "/runs/stream"),
        ("POST", "/runs/crons"),
        ("PUT", "/store/items"),
    ]:
        assert (m, p) in mounted
        assert is_protocol_path(_concrete(p)) and not protocol_route_allowed(m, _concrete(p)), f"{m} {p}"


def test_every_allowed_run_creating_route_counts_against_the_run_limit() -> None:
    creating = [p for m, p in PROTOCOL_ALLOWLIST if m == "POST" and p.rsplit("/", 1)[-1] in ("runs", "stream", "wait")]
    assert len(creating) == 3
    for p in creating:
        assert _RUN_START.match(_concrete(p)), p


@pytest.mark.asyncio
async def test_the_middleware_refuses_everything_else() -> None:
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = ProtocolAllowlist(inner)

    async def call(method: str, path: str) -> int:
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        scope = {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}
        await mw(scope, receive, send)
        return int(next(m for m in sent if m["type"] == "http.response.start")["status"])

    assert await call("POST", "/threads/t1/runs/stream") == 200
    assert await call("POST", "/threads/t1/runs/r1/cancel") == 200
    assert await call("GET", "/api/me") == 200
    assert await call("GET", "/learn/ch01") == 200  # the SPA
    for method, path in [
        ("POST", "/threads/t1/commands"),
        ("POST", "/threads/t1/stream/events"),
        ("POST", "/threads/t1/state"),
        ("POST", "/threads//state"),
        ("POST", "/threads/t1/state/"),
        ("PATCH", "/threads/t1"),
        ("GET", "/threads/t1"),
        ("POST", "/threads/search"),
        ("POST", "/runs/stream"),
        ("POST", "/runs/crons"),
        ("GET", "/store/items"),
        ("POST", "/assistants/search"),
        ("POST", "/threads/t1/runs/r1/extra"),
    ]:
        assert await call(method, path) == 403, f"{method} {path}"
