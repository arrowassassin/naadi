"""Rate-limit audit (segment 22): every API operation carries a limiter, and heavy ones a strict one.

The audit walks the app's routes (every operation of the OpenAPI document, plus the ones kept out of it such as
`/api/csp-report`) and resolves each route's dependency tree. A route counts as limited when the tree holds:

- a dependency tagged by `auth_limits.rate_limited` / `ip_limited` / `require_admin` (`__naadi_rate_limit__`);
- a module's own `limited(bucket)` closure (the courses/qbank/cards/plan/notes/ink pattern: a free `bucket`);
- or a literal `<limiters>["<bucket>"].hit(` in the endpoint's or a dependency's source, or in a same-module
  helper either one calls.

Every bucket found must exist in `app.state.rate_limiters`. Anonymous routes must be keyed per client (ip).
Heavy routes must have a bucket at or under their ceiling. The results table in docs/SECURITY.md is generated
from `audit()` (`python -m tests.test_rate_limit_audit` prints it).
"""

from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from naadi_api.auth import get_account_user, get_current_user
from naadi_api.auth_limits import RATE_LIMIT_TAG, RateLimiter
from naadi_api.db import Database
from naadi_api.main import create_app

from .conftest import fake_service, make_settings

INLINE_HIT = re.compile(r"""\[\s*["']([a-z0-9_]+)["']\s*\]\.hit\(""")
PARAM_HIT = re.compile(r"\[\s*bucket\s*\]\.hit\(")
CALLED = re.compile(r"\b([A-Za-z_]\w*)\(")
WRITES_TAG = "__naadi_rate_limit_writes__"  # require_admin's extra bucket for non-GET methods
SPA_FALLBACKS = {"api_not_found", "spa"}

# Ceilings in requests per minute per key. A route matching several rules takes the strictest.
HEAVY_RULES: tuple[tuple[str, Callable[[str, str], bool], int], ...] = (
    (
        "ingest/upload/import",
        lambda m, p: m == "POST" and bool(re.search(r"/(ingest|uploads?|library|import)\b", p)),
        30,
    ),
    # Starting or producing an export; polling a job's status (GET /api/export/{id}) is not heavy.
    (
        "export",
        lambda m, p: (
            (m == "POST" and "export" in p)
            or (m == "GET" and bool(re.search(r"/export(\.\w+)?$", p)) and not p.endswith("/api/export"))
        ),
        30,
    ),
    (
        "generation",
        lambda m, p: m != "GET" and not p.startswith("/api/admin") and bool(re.search(r"/(generat\w*|gen)\b", p)),
        30,
    ),
    ("admin write", lambda m, p: m != "GET" and p.startswith("/api/admin"), 60),
    ("auth/account write", lambda m, p: m != "GET" and p in ("/api/me",), 60),
)
DEFAULT_CEILING = 600  # nothing per user may exceed this (the per-client floor's rate)


@dataclass
class RouteAudit:
    method: str
    path: str
    operation_id: str | None
    buckets: list[tuple[str, str]] = field(default_factory=list)  # (bucket, keyed_by user|ip)
    authenticated: bool = False
    ceiling: int = DEFAULT_CEILING
    heavy: str | None = None


def _walk(routes: list[Any]) -> Iterator[APIRoute]:
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):  # FastAPI >= 0.16x wraps included routers lazily
            yield from _walk(r.original_router.routes)


def _calls(dependant: Any) -> Iterator[Callable[..., Any]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _calls(sub)


def _buckets_of(fn: Callable[..., Any], authenticated: bool) -> list[tuple[str, str]]:
    tag = getattr(fn, RATE_LIMIT_TAG, None)
    if tag:
        return [tuple(tag)]  # type: ignore[list-item]
    found: list[tuple[str, str]] = []
    code = getattr(fn, "__code__", None)
    if code is not None and "bucket" in code.co_freevars and fn.__closure__:
        found.append((fn.__closure__[code.co_freevars.index("bucket")].cell_contents, "user"))
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = ""
    key = "user" if authenticated else "ip"
    found += [(b, key) for b in INLINE_HIT.findall(src)]
    # One level into same-module helpers the function calls (e.g. config_api's `_hit_admin_limit(request, ...)`).
    module = getattr(fn, "__module__", None)
    for name in set(CALLED.findall(src)):
        helper = getattr(fn, "__globals__", {}).get(name)
        if inspect.isfunction(helper) and helper.__module__ == module and helper is not fn:
            with contextlib.suppress(OSError, TypeError):
                helper_src = inspect.getsource(helper)
                found += [(b, key) for b in INLINE_HIT.findall(helper_src)]
                # A helper that takes the bucket as an argument (`_limit(request, "export_start", user_id)`).
                if PARAM_HIT.search(helper_src):
                    lit = re.compile(rf"\b{re.escape(name)}\(\s*\w+\s*,\s*[\"']([a-z0-9_]+)[\"']")
                    found += [(b, key) for b in lit.findall(src)]
    return found


def audit(app: FastAPI) -> list[RouteAudit]:
    out = []
    for route in _walk(app.router.routes):
        if getattr(route.endpoint, "__name__", "") in SPA_FALLBACKS:
            continue
        calls = list(_calls(route.dependant))
        authenticated = any(c in (get_current_user, get_account_user) for c in calls)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            # FastAPI names an operation without an explicit operation_id after route.unique_id.
            op_id = route.operation_id or route.unique_id
            a = RouteAudit(method, route.path, op_id, authenticated=authenticated)
            for fn in [*calls, route.endpoint]:
                found = _buckets_of(fn, authenticated)
                writes = getattr(fn, WRITES_TAG, None)
                if writes and method != "GET":
                    found.append(tuple(writes))  # type: ignore[arg-type]
                for b in found:
                    if b not in a.buckets:
                        a.buckets.append(b)
            for name, match, ceiling in HEAVY_RULES:
                if match(method, route.path) and ceiling < a.ceiling:
                    a.heavy, a.ceiling = name, ceiling
            out.append(a)
    return out


def _app() -> FastAPI:
    return create_app(make_settings(), health=fake_service(), db=Database(None))


def per_minute(lim: RateLimiter) -> float:
    return lim.limit * 60.0 / lim.window_s


def test_every_operation_is_audited() -> None:
    app = _app()
    audited = {(a.method, a.operation_id) for a in audit(app)}
    spec = app.openapi()
    for path, item in spec["paths"].items():
        for method, op in item.items():
            assert (method.upper(), op["operationId"]) in audited, f"{method.upper()} {path} missed by the audit walk"
    assert ("POST", "postCspReport") in audited  # out of the schema, still audited


def test_every_route_has_a_registered_limiter() -> None:
    app = _app()
    limiters: dict[str, RateLimiter] = app.state.rate_limiters
    problems = []
    for a in audit(app):
        where = f"{a.method} {a.path} ({a.operation_id})"
        if not a.buckets:
            problems.append(f"{where}: no rate limit")
            continue
        for bucket, _ in a.buckets:
            if bucket not in limiters:
                problems.append(f"{where}: bucket {bucket!r} is not registered in app.state.rate_limiters")
        if not a.authenticated and not any(k == "ip" for _, k in a.buckets):
            problems.append(f"{where}: anonymous route without a per-client (ip_limited) bucket")
    assert problems == []


def test_limits_are_within_ceilings() -> None:
    app = _app()
    limiters: dict[str, RateLimiter] = app.state.rate_limiters
    problems = []
    for a in audit(app):
        rates = [per_minute(limiters[b]) for b, _ in a.buckets if b in limiters]
        if rates and min(rates) > a.ceiling:
            kind = a.heavy or "default"
            problems.append(f"{a.method} {a.path}: strictest bucket {min(rates):.0f}/min > {kind} ceiling {a.ceiling}")
    assert problems == []


def test_heavy_rules_match_the_routes_we_know_about() -> None:
    by_op = {a.operation_id: a for a in audit(_app())}
    assert by_op["exportPlanCsv"].heavy == "export"
    assert by_op["putAppConfig"].heavy == "admin write"
    assert by_op["updateMe"].heavy == "auth/account write"
    assert by_op["getCourses"].heavy is None
    assert by_op["getExport"].heavy is None  # polling an export job is not heavy; starting one is
    assert by_op["startExport"].heavy == "export"


def markdown_table(app: FastAPI | None = None) -> str:
    app = app or _app()
    limiters: dict[str, RateLimiter] = app.state.rate_limiters
    rows = ["| Method | Path | Buckets (limit/window, key) | Class | Ceiling/min |", "|---|---|---|---|---|"]
    for a in sorted(audit(app), key=lambda x: (x.path, x.method)):
        bs = ", ".join(
            f"`{b}` {limiters[b].limit}/{limiters[b].window_s:.0f}s {k}" if b in limiters else f"`{b}` (missing)"
            for b, k in a.buckets
        )
        rows.append(f"| {a.method} | `{a.path}` | {bs or '**none**'} | {a.heavy or '-'} | {a.ceiling} |")
    return "\n".join(rows)


if __name__ == "__main__":
    print(markdown_table())
