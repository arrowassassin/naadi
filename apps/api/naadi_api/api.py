"""`/api` router: system endpoints. Feature routers are added by later segments."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .db import Database
from .errors import ErrorResponse
from .health.models import HealthResponse, ReadyResponse
from .health.service import HealthService

router = APIRouter(prefix="/api")


@router.get(
    "/health",
    operation_id="getHealth",
    summary="System health: database, storage, OpenRouter quota, Tavily credits",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "The database is down"}, 500: {"model": ErrorResponse}},
    tags=["system"],
)
async def health(request: Request) -> JSONResponse:
    service: HealthService = request.app.state.health
    result = await service.health()
    status_code = 503 if result.status == "down" else 200
    return JSONResponse(result.model_dump(mode="json"), status_code=status_code)


@router.get(
    "/ready",
    operation_id="getReady",
    summary="Readiness: settings loaded and the database pool can hand out a connection",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "Not ready"}},
    tags=["system"],
)
async def ready(request: Request) -> JSONResponse:
    db: Database = request.app.state.db
    checks = {"settings": request.app.state.settings is not None, "database_pool": False}
    if db.configured:
        try:
            await db.ping(deadline_s=2.0)
            checks["database_pool"] = True
        except Exception:  # noqa: BLE001 - readiness is a boolean; /api/health has the detail
            checks["database_pool"] = False
    ok = all(checks.values())
    return JSONResponse(ReadyResponse(ready=ok, checks=checks).model_dump(mode="json"), status_code=200 if ok else 503)
