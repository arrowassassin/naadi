"""App factory. `uvicorn naadi_api.main:app` serves the SPA at `/` and the API at `/api/*`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from . import api
from .config import load_models_config
from .db import Database
from .errors import install_error_handlers
from .health.checks import database_check, nvidia_check, openrouter_check, storage_check, tavily_check
from .health.service import Check, HealthService
from .logs import configure_logging, get_logger
from .middleware import RequestContextMiddleware
from .settings import Settings, get_settings
from .spa import install_spa

log = get_logger("naadi.app")
NVIDIA_PROBE_TTL_S = 3600.0


def package_version() -> str:
    try:
        return version("naadi-api")
    except PackageNotFoundError:
        return "0.0.0"


def default_health_service(settings: Settings, db: Database) -> HealthService:
    models = load_models_config(settings.config_dir)
    return HealthService(
        checks={
            "database": Check("database", database_check(db), ttl_s=5.0),
            "storage": Check("storage", storage_check(settings), ttl_s=60.0),
            "openrouter": Check("openrouter", openrouter_check(settings), ttl_s=60.0),
            # Real model calls: cached for an hour whatever the result, so /api/health cannot burn quota.
            "nvidia": Check(
                "nvidia",
                nvidia_check(settings, models.models_for("nvidia")),
                ttl_s=NVIDIA_PROBE_TTL_S,
                timeout_s=20.0,
                failure_ttl_s=NVIDIA_PROBE_TTL_S,
            ),
            "tavily": Check("tavily", tavily_check(settings, models.search.monthly_credit_guard), ttl_s=300.0),
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
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    db = db or Database(settings.database_url)
    health = health or default_health_service(settings, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.open()
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
    app.include_router(api.router)
    install_spa(app, settings.static_dir)
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
