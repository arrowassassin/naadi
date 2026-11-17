"""The Space's top-level ASGI app: Aegra (Agent Protocol server) hosting the Naadi FastAPI app.

    uvicorn naadi_api.agent.host:app --workers 1

`aegra.json` registers the graphs, the auth handler and `http.app = naadi_api.agent.host:naadi_app`. Aegra
mounts its protocol routes at the root (`/assistants`, `/threads`, `/runs`, `/store`, plus `/info`,
`/health`, `/ready`, `/live`), so this module:

- builds the Naadi app once (`naadi_app`), then lets Aegra add its routes, middleware and lifespan;
- moves the SPA fallback and the `/api/*` 404 to the END of the route table (Aegra's routes are appended
  after ours, and a catch-all registered first would shadow `GET /threads/...`), and drops Aegra's JSON
  root handler so `/` stays the SPA;
- fails closed if the Supabase auth handler did not load (Aegra would otherwise fall back to a single
  shared anonymous identity);
- starts the agent runtime (LLM gateway, search) before Aegra's cron scheduler can fire, and ensures the
  hourly system `ping` cron;
- validates cron owners against `auth.users` (Aegra's documented hook) and keeps the system identity.

The in-memory broker needs exactly one worker process; the Dockerfile runs one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..settings import APP_DIR

# Aegra reads its settings from the environment when first imported: set safe defaults before that.
_AEGRA_DEFAULTS = {
    "AEGRA_CONFIG": str(APP_DIR / "aegra.json"),
    "AUTH_TYPE": "custom",
    "REDIS_BROKER_ENABLED": "false",
    "ENABLE_PROMETHEUS_METRICS": "false",  # /metrics would be public
    "OTEL_TARGETS": "",
    "OTEL_CONSOLE_EXPORT": "false",
    # Naadi's startup migrates Aegra's tables first (naadi_api.agent.migrate_all), before workers start.
    "RUN_MIGRATIONS_ON_STARTUP": "false",
    "PROJECT_NAME": "Naadi agent runtime",
    "LOG_VERBOSITY": "standard",
    # Connection budget. Aegra's defaults (SQLAlchemy 10 + 20 overflow, LangGraph up to 20) would open ~50
    # connections per process: two processes exhausted the local Postgres (max_connections 100) under the
    # e2e matrix, and Supabase Free's pooler allows far fewer. Per process now: Aegra metadata 2 + 2,
    # LangGraph checkpoints/store 1..4, Naadi API 3, agent runtime 4 (= at most 15).
    "SQLALCHEMY_POOL_SIZE": "2",
    "SQLALCHEMY_MAX_OVERFLOW": "2",
    "LANGGRAPH_MIN_POOL_SIZE": "1",
    "LANGGRAPH_MAX_POOL_SIZE": "4",
}
for _k, _v in _AEGRA_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

from fastapi import FastAPI  # noqa: E402
from starlette.routing import BaseRoute  # noqa: E402

from ..logs import configure_logging, get_logger  # noqa: E402
from ..main import create_app  # noqa: E402
from ..settings import get_settings  # noqa: E402
from .identity import SYSTEM_IDENTITY  # noqa: E402

log = get_logger("naadi.agent.host")

HOSTED = False
SPA_ENDPOINTS = ("api_not_found", "spa")
SYSTEM_CRON_KEY = "naadi.ping.hourly"
SYSTEM_CRON_SCHEDULE = "17 * * * *"
# (key, graph, schedule UTC, input, fire on first boot). Owned by SYSTEM_IDENTITY; created once.
SYSTEM_CRONS: tuple[tuple[str, str, str, dict[str, Any], bool], ...] = (
    (SYSTEM_CRON_KEY, "ping", SYSTEM_CRON_SCHEDULE, {"trigger": "cron"}, True),
    ("naadi.tavily_usage.daily", "tavily_usage", "43 3 * * *", {}, False),
    ("naadi.coach.nightly", "coach", "7 21 * * *", {}, False),  # 02:37 IST
    ("naadi.youtube_map.weekly", "youtube_map", "17 5 * * 0", {}, False),  # Sunday 05:17 UTC (S20)
    # Handwriting OCR (S13): idle layers every two minutes; deferred ones after the 00:00 UTC budget reset.
    ("naadi.ink_ocr.sweep", "ink_ocr", "*/2 * * * *", {"mode": "sweep"}, False),
    ("naadi.ink_ocr.nightly", "ink_ocr", "23 0 * * *", {"mode": "nightly"}, False),
    # Question authoring (S21): drafts into the human review queue within the job reserve, then item statistics.
    ("naadi.author.nightly", "author", "41 22 * * *", {"mode": "nightly"}, False),
    ("naadi.author.psychometrics", "author", "11 23 * * *", {"mode": "psychometrics"}, False),
    # Planner (S16): the daily Web Push reminders, per student's local time (a 15-minute tick; once per device per day).
    ("naadi.planner.reminders", "planner", "*/15 * * * *", {"mode": "reminders"}, False),
    # Ops (S22): encrypted backups to the private HF dataset (records not_configured until the owner sets the token
    # and key), then the retention pass.
    ("naadi.backup.nightly", "backup", "0 3 * * *", {"mode": "db"}, False),
    ("naadi.backup.storage_weekly", "backup", "30 3 * * 0", {"mode": "storage"}, False),  # Sunday 03:30 UTC
    ("naadi.prune.daily", "prune", "11 4 * * *", {}, False),
    # Pathway (S17): the official pages behind the pathway tasks, Mondays 04:13 UTC (09:43 IST).
    ("naadi.pathway_verify.weekly", "pathway_verify", "13 4 * * 1", {}, False),
)

naadi_app: FastAPI = create_app()


def _endpoint_name(route: BaseRoute) -> str | None:
    ep = getattr(route, "endpoint", None)
    return getattr(ep, "__name__", None)


def reorder_routes(app: FastAPI) -> None:
    """SPA fallbacks last; Aegra's `/` JSON root removed (the SPA serves `/`)."""
    routes = app.router.routes
    tail = [r for r in routes if _endpoint_name(r) in SPA_ENDPOINTS]
    keep = [
        r
        for r in routes
        if _endpoint_name(r) not in SPA_ENDPOINTS
        and not (getattr(r, "path", None) == "/" and _endpoint_name(r) == "root_handler")
    ]
    routes[:] = [*keep, *tail]


async def validate_cron_user(user_id: str) -> bool:
    """A cron fires only for the system identity or a user who still exists in auth.users."""
    if user_id == SYSTEM_IDENTITY:
        return True
    import uuid

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return False
    from .runtime import agent_db

    db = agent_db()
    if db.pool is None:
        await db.open()
    if db.pool is None:
        return False
    async with db.pool.connection(timeout=5.0) as conn:
        cur = await conn.execute("select 1 from auth.users where id = %s and deleted_at is null", (uid,))
        return await cur.fetchone() is not None


async def ensure_system_crons() -> None:
    """Idempotently create the system crons (owned by SYSTEM_IDENTITY): hourly `ping`, daily `tavily_usage`."""
    from aegra_api.core.database import db_manager
    from aegra_api.core.orm import Cron as CronORM
    from aegra_api.models.crons import CronCreate
    from aegra_api.services.cron_service import CronService
    from aegra_api.services.langgraph_service import get_langgraph_service
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    for key, graph, schedule, payload, fire_now in SYSTEM_CRONS:
        async with AsyncSession(db_manager.get_engine()) as session:
            # Two processes starting together must not both create it: serialize on a transaction lock.
            await session.execute(text("select pg_advisory_xact_lock(4611686018427387914)"))
            existing = await session.scalar(
                select(CronORM).where(
                    CronORM.user_id == SYSTEM_IDENTITY,
                    text("crons.metadata ->> 'naadi_key' = :key").bindparams(key=key),
                )
            )
            if existing is not None:
                continue
            cron = await CronService(session, get_langgraph_service()).create_cron(
                CronCreate(
                    assistant_id=graph,
                    schedule=schedule,
                    input=payload,
                    metadata={"naadi_key": key},
                    on_run_completed="delete",
                    timezone="UTC",
                ),
                SYSTEM_IDENTITY,
            )
            if fire_now:
                # The service schedules the second occurrence (the API would fire the first itself): fire on
                # the next poll instead, so a fresh deployment reports a cron ping within a minute.
                await session.execute(
                    text("update crons set next_run_date = now() where cron_id = :id").bindparams(id=cron.cron_id)
                )
            await session.commit()
            log.info("agent_runtime.system_cron_created", key=key, schedule=schedule)


def _wrap_lifespan(app: FastAPI) -> None:
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[None]:
        from .runtime import close_runtime, init_runtime

        rt = await init_runtime(get_settings())
        from ..assistant import memory as assistant_memory
        from ..llm_privacy import PrivacyDirectory

        assistant_memory.set_privacy(PrivacyDirectory(rt.db))
        try:
            async with inner(a):
                await ensure_system_crons()
                yield
        finally:
            await close_runtime()

    app.router.lifespan_context = lifespan


def build() -> FastAPI:
    global HOSTED
    from aegra_api.core.auth_middleware import get_auth_instance
    from aegra_api.main import app as aegra_app
    from aegra_api.services import cron_scheduler

    if aegra_app is not naadi_app:
        raise RuntimeError(
            "Aegra did not load naadi_api.agent.host:naadi_app (check AEGRA_CONFIG / aegra.json http.app)"
        )
    if get_auth_instance() is None:
        raise RuntimeError("Aegra auth handler did not load: refusing to serve with a shared anonymous identity")
    cron_scheduler._validate_cron_user = validate_cron_user  # Aegra's documented liveness hook
    # Long-term memory (S14): the store's semantic index uses the local fastembed engine. aegra.json can only name
    # a hosted embeddings provider, so the loader Aegra reads when it opens the store is replaced here.
    import aegra_api.core.database as aegra_database

    from ..assistant.store_index import store_config

    aegra_database.load_store_config = store_config  # type: ignore[assignment,attr-defined]
    # The agent auth handler uses the app's own verifier and account directory (one DB pool, one cache).
    from . import auth as agent_auth
    from .auth import AuthUnavailableAs503

    agent_auth.configure(verifier=aegra_app.state.auth, accounts=aegra_app.state.accounts)
    aegra_app.add_middleware(AuthUnavailableAs503)
    # Per-student limits on Aegra's own protocol routes (segment 22; the Naadi routes have their own buckets).
    from .limits import AgentRateLimit

    aegra_app.add_middleware(AgentRateLimit)
    # Clients never write graph state directly (review14): the tutor's guard must see every turn.
    from .auth import ThreadStateWritesDenied

    aegra_app.add_middleware(ThreadStateWritesDenied)
    from ..health.checks import agent_runtime_check
    from ..health.service import Check

    health = aegra_app.state.health
    health.checks["agent_runtime"] = Check(
        "agent_runtime", agent_runtime_check(aegra_app.state.db), ttl_s=30.0, timeout_s=8.0
    )
    # SearXNG runs beside the API only in the Space image (start.sh); elsewhere there is nothing to report.
    from pathlib import Path

    from ..config import load_models_config
    from ..health.checks import SEARXNG_BIN, searxng_check

    if Path(SEARXNG_BIN).exists():
        layer = (
            (load_models_config(get_settings().config_dir).search.model_extra or {}).get("layers", {}).get("searxng")
        )
        base = (layer or {}).get("base_url") or "http://127.0.0.1:8888"
        health.checks["searxng"] = Check("searxng", searxng_check(base), ttl_s=30.0, timeout_s=4.0)
    reorder_routes(aegra_app)
    _wrap_lifespan(aegra_app)
    configure_logging(get_settings().log_level)  # Aegra reconfigures logging on import; restore JSON lines
    HOSTED = True
    return aegra_app


app = build()
