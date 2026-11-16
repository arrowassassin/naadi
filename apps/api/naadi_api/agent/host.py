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


async def ensure_system_cron() -> None:
    """Idempotently create the hourly system `ping` cron (owned by SYSTEM_IDENTITY)."""
    from aegra_api.core.database import db_manager
    from aegra_api.core.orm import Cron as CronORM
    from aegra_api.models.crons import CronCreate
    from aegra_api.services.cron_service import CronService
    from aegra_api.services.langgraph_service import get_langgraph_service
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_manager.get_engine()) as session:
        # Two processes starting together must not both create it: serialize on a transaction lock.
        await session.execute(text("select pg_advisory_xact_lock(4611686018427387914)"))
        existing = await session.scalar(
            select(CronORM).where(
                CronORM.user_id == SYSTEM_IDENTITY,
                text("crons.metadata ->> 'naadi_key' = :key").bindparams(key=SYSTEM_CRON_KEY),
            )
        )
        if existing is not None:
            return
        cron = await CronService(session, get_langgraph_service()).create_cron(
            CronCreate(
                assistant_id="ping",
                schedule=SYSTEM_CRON_SCHEDULE,
                input={"trigger": "cron"},
                metadata={"naadi_key": SYSTEM_CRON_KEY},
                on_run_completed="delete",
                timezone="UTC",
            ),
            SYSTEM_IDENTITY,
        )
        # The service schedules the second occurrence (the API would fire the first itself): fire on the next
        # scheduler poll instead, so a fresh deployment reports a cron ping within a minute.
        await session.execute(
            text("update crons set next_run_date = now() where cron_id = :id").bindparams(id=cron.cron_id)
        )
        await session.commit()
        log.info("agent_runtime.system_cron_created", schedule=SYSTEM_CRON_SCHEDULE)


def _wrap_lifespan(app: FastAPI) -> None:
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[None]:
        from .runtime import close_runtime, init_runtime

        await init_runtime(get_settings())
        try:
            async with inner(a):
                await ensure_system_cron()
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
    # The agent auth handler uses the app's own verifier and account directory (one DB pool, one cache).
    from . import auth as agent_auth
    from .auth import AuthUnavailableAs503

    agent_auth.configure(verifier=aegra_app.state.auth, accounts=aegra_app.state.accounts)
    aegra_app.add_middleware(AuthUnavailableAs503)
    from ..health.checks import agent_runtime_check
    from ..health.service import Check

    health = aegra_app.state.health
    health.checks["agent_runtime"] = Check(
        "agent_runtime", agent_runtime_check(aegra_app.state.db), ttl_s=30.0, timeout_s=8.0
    )
    reorder_routes(aegra_app)
    _wrap_lifespan(aegra_app)
    configure_logging(get_settings().log_level)  # Aegra reconfigures logging on import; restore JSON lines
    HOSTED = True
    return aegra_app


app = build()
