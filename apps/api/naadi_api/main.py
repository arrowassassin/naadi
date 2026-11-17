"""App factory. `uvicorn naadi_api.main:app` serves the SPA at `/` and the API at `/api/*`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from . import __version__, api
from .anki.routes import install_anki_routes
from .auth import AccountDirectory, TokenVerifier, install_auth
from .auth_routes import install_auth_routes
from .authoring.routes import install_authoring_routes
from .cards.routes import install_cards_routes
from .config import load_models_config
from .config_api.routes import install_app_config
from .courses import install_courses_routes
from .db import Database
from .errors import install_error_handlers
from .export.routes import install_export_routes, start_export_gc, stop_export_gc
from .generation.health import generation_check
from .generation.routes import install_generation_routes
from .health.checks import (
    database_check,
    nvidia_check,
    openrouter_check,
    storage_check,
    tavily_check,
    tavily_db_reader,
)
from .health.service import Check, HealthService
from .ink.routes import install_ink_routes, start_ink_gc, stop_ink_gc
from .logs import configure_logging, get_logger, register_secrets
from .middleware import RequestContextMiddleware
from .notes.routes import install_notes_routes
from .plan.routes import install_plan_routes
from .qbank.routes import install_qbank_routes
from .search_routes import install_search_routes, start_search, stop_search
from .seed.jobs import drain_seed_tasks
from .seed.jobs import router as seed_router
from .settings import Settings, get_settings
from .spa import install_spa
from .youtube.routes import install_youtube_routes

log = get_logger("naadi.app")
NVIDIA_PROBE_TTL_S = 3600.0


def package_version() -> str:
    return __version__


def default_health_service(settings: Settings, db: Database) -> HealthService:
    models = load_models_config(settings.config_dir)
    nvidia_timeout = models.providers["nvidia"].probe_timeout_s if "nvidia" in models.providers else 30.0
    return HealthService(
        checks={
            "database": Check("database", database_check(db), ttl_s=5.0),
            "storage": Check("storage", storage_check(settings), ttl_s=60.0),
            "openrouter": Check("openrouter", openrouter_check(settings), ttl_s=60.0),
            # Real model calls: cached for an hour whatever the result, so /api/health cannot burn quota.
            "nvidia": Check(
                "nvidia",
                nvidia_check(settings, models.models_for("nvidia"), timeout_s=nvidia_timeout),
                ttl_s=NVIDIA_PROBE_TTL_S,
                timeout_s=nvidia_timeout + 5.0,  # probes run concurrently; each has its own deadline
                failure_ttl_s=NVIDIA_PROBE_TTL_S,
            ),
            "tavily": Check(
                "tavily", tavily_check(settings, models.search.monthly_credit_guard, tavily_db_reader(db)), ttl_s=60.0
            ),
            "generation": Check("generation", generation_check(settings, db), ttl_s=30.0),
        },
        version=settings.app_version,
        environment=settings.app_env,
        secrets=settings.secret_values(),
    )


def create_app(
    settings: Settings | None = None,
    *,
    health: HealthService | None = None,
    db: Database | None = None,
    verifier: TokenVerifier | None = None,
    accounts: AccountDirectory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    register_secrets(settings.secret_values())
    configure_logging(settings.log_level)
    db = db or Database(settings.database_url)
    health = health or default_health_service(settings, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.open()
        await app.state.app_config.start()
        await start_search(app)
        await start_ink_gc(app)
        await start_export_gc(app)
        log.info(
            "startup",
            environment=settings.app_env,
            version=settings.app_version,
            database_configured=db.configured,
            static_dir_present=settings.static_dir.is_dir(),
        )
        try:
            yield
        finally:
            await drain_seed_tasks(app)
            await stop_ink_gc(app)
            await app.state.authoring_jobs.drain()
            await stop_export_gc(app)
            await stop_search(app)
            await app.state.app_config.stop()
            await db.close()
            log.info("shutdown")

    app = FastAPI(
        title="Naadi API",
        version=package_version(),
        openapi_url="/api/openapi.json",
        docs_url=None,  # Swagger UI needs a CDN + inline scripts, which the CSP forbids
        redoc_url=None,
        lifespan=lifespan,
        debug=False,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.health = health

    install_error_handlers(app)
    install_auth(app, settings, verifier, accounts)
    app.include_router(api.router)
    install_auth_routes(app)
    install_courses_routes(app)
    install_qbank_routes(app)
    install_cards_routes(app)
    install_anki_routes(app)
    install_youtube_routes(app)
    install_plan_routes(app)
    install_search_routes(app)
    install_ink_routes(app)
    install_notes_routes(app)
    install_authoring_routes(app)
    install_export_routes(app)
    install_app_config(app, settings)
    install_generation_routes(app)  # needs app.state.app_config
    app.include_router(seed_router)
    install_spa(app, settings.static_dir)
    # Chapter JSON is large and repetitive; Starlette's GZip skips text/event-stream, so SSE is untouched.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """`naadi_api.main:app` is built on first access (PEP 562), so importing this module for
    `create_app` (tests, OpenAPI export) never reads the environment."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
