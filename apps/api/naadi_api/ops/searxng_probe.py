"""`GET /api/admin/search/searxng-probe` (segment 22): test the self-hosted SearXNG before enabling search layer 8.

SearXNG listens on the container's loopback only, so this admin-only route is how it is checked from outside: one
fixed medical query through the same request shape and parser students' searches would use (`format=json`,
naadi_api/search.py `parse_searxng`), returning counts, the engines that answered and the latency. It never turns the
layer on (that stays `app_config.search.layers.searxng.enabled`) and stores nothing.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..auth import AdminUser
from ..auth_limits import RateLimiter, rate_limited, register_limiters
from ..config import load_models_config
from ..search import parse_searxng

router = APIRouter(prefix="/api/admin/search", tags=["admin"])

PROBE_QUERY = "myocardial infarction troponin"
PROBE_TIMEOUT_S = 8.0

Outcome = Literal["ok", "no_results", "unreachable", "http_error", "bad_response"]


class SearxngProbe(BaseModel):
    outcome: Outcome
    base_url: str = Field(description="Where the API reaches SearXNG (loopback on the Space)")
    layer_enabled: bool = Field(description="app_config search.layers.searxng.enabled as shipped in config")
    results: int = Field(ge=0, description="https results the parser keeps")
    engines: list[str] = Field(default_factory=list, max_length=20, description="Engines that returned results")
    unresponsive: list[str] = Field(default_factory=list, max_length=20, description="Engines SearXNG reports failing")
    latency_ms: int | None = None
    http_status: int | None = None


def _layer(request: Request) -> dict[str, Any]:
    models = load_models_config(request.app.state.settings.config_dir)
    layers = (models.search.model_extra or {}).get("layers", {})
    return dict(layers.get("searxng") or {})


@router.get(
    "/searxng-probe",
    operation_id="getSearxngProbe",
    summary="Admin only: one fixed query through the self-hosted SearXNG, parsed like a student search",
    response_model=SearxngProbe,
    dependencies=[rate_limited("searxng_probe")],
)
async def searxng_probe(request: Request, _admin: AdminUser) -> SearxngProbe:
    layer = _layer(request)
    base = str(layer.get("base_url") or "http://127.0.0.1:8888").rstrip("/")
    common: dict[str, Any] = {"base_url": base, "layer_enabled": bool(layer.get("enabled")), "results": 0}
    started = time.perf_counter()
    try:
        # Raw httpx: SearXNG has no SDK; this is the same JSON GET search.py's `_searxng` makes.
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as http:
            r = await http.get(f"{base}/search", params={"q": PROBE_QUERY, "format": "json", "safesearch": 1})
    except httpx.HTTPError:
        return SearxngProbe(outcome="unreachable", **common)
    latency = round((time.perf_counter() - started) * 1000)
    if r.status_code != 200:
        return SearxngProbe(outcome="http_error", http_status=r.status_code, latency_ms=latency, **common)
    try:
        body = r.json()
    except ValueError:
        return SearxngProbe(outcome="bad_response", http_status=200, latency_ms=latency, **common)
    kept = parse_searxng(body, int(layer.get("max_results") or 5))
    engines = sorted({str(e) for res in body.get("results") or [] for e in (res.get("engines") or [])})[:20]
    unresponsive = sorted({str(u[0]) for u in body.get("unresponsive_engines") or [] if u})[:20]
    common["results"] = len(kept)
    return SearxngProbe(
        outcome="ok" if kept else "no_results",
        engines=engines,
        unresponsive=unresponsive,
        latency_ms=latency,
        http_status=200,
        **common,
    )


def install_searxng_probe(app: Any) -> None:
    register_limiters(app, {"searxng_probe": RateLimiter(6, 60.0)})
    app.include_router(router)
