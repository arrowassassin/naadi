"""HTTP for `app_config`: the admin editor (with history). Clients get flags, nav, exam rules and UI copy from
GET /api/public-config (auth_routes.py), which reads the same AppConfigService."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..auth import AdminUser
from ..errors import ErrorResponse, FieldError, error_response
from ..logs import get_logger
from ..settings import Settings
from .schemas import KEY_SPECS, UnknownConfigKey
from .service import AppConfigService, ConfigRow, CrossKeyInvalid, HistoryRow, StaleWrite

log = get_logger("naadi.app_config.http")

KEY_PATTERN = r"^[a-z][a-z0-9_.]{1,47}$"
MAX_PUT_BYTES = 64 * 1024

router = APIRouter(prefix="/api")


class ConfigKeySummary(BaseModel):
    key: str
    description: str
    public: bool = Field(description="Served to clients with GET /api/public-config")
    source: str = Field(description="'db' when a valid row exists, else 'file' (config/*.yaml fallback)")
    source_file: str
    updated_at: dt.datetime | None
    updated_by: uuid.UUID | None
    invalid_reason: str | None


class ConfigKeyList(BaseModel):
    items: list[ConfigKeySummary]


class ConfigEntry(BaseModel):
    key: str
    value: JsonValue
    source: str
    updated_at: dt.datetime | None = Field(description="Send back as expected_updated_at when saving")
    updated_by: uuid.UUID | None
    admin_edited_at: dt.datetime | None = Field(description="Set while the seed must not overwrite this key")
    invalid_reason: str | None
    json_schema: dict[str, JsonValue] = Field(description="JSON Schema of the value (for the admin editor)")


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: JsonValue
    expected_updated_at: dt.datetime | None = Field(
        description="updated_at of the row you edited (null if it had no row); a mismatch is a 409"
    )
    admin_edited: bool = Field(
        default=True,
        description="Mark the key as admin-edited (the seed then keeps it). false hands it back to the seed,"
        " e.g. after restoring the shipped value",
    )


class ConfigHistoryItem(BaseModel):
    id: int
    value: JsonValue
    updated_by: uuid.UUID | None
    updated_at: dt.datetime | None
    op: str
    replaced_at: dt.datetime


class ConfigHistory(BaseModel):
    key: str
    items: list[ConfigHistoryItem]


_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "Not an admin"},
    404: {"model": ErrorResponse, "description": "Unknown config key"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}

ConfigKey = Annotated[str, Path(pattern=KEY_PATTERN, description="app_config key, e.g. feature_flags")]


def _service(request: Request) -> AppConfigService:
    s: AppConfigService = request.app.state.app_config
    return s


def _hit_admin_limit(request: Request, admin_id: uuid.UUID) -> None:
    limiters = getattr(request.app.state, "rate_limiters", None)
    if limiters and "admin" in limiters:
        limiters["admin"].hit(str(admin_id))


async def _body_within_limit(request: Request) -> None:
    """Refuse oversized admin writes before parsing them (413); the declared length is checked first."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_PUT_BYTES:
        raise HTTPException(413, f"The value is larger than {MAX_PUT_BYTES // 1024} KB.")
    if len(await request.body()) > MAX_PUT_BYTES:
        raise HTTPException(413, f"The value is larger than {MAX_PUT_BYTES // 1024} KB.")


def _known(key: str) -> None:
    if key not in KEY_SPECS:
        raise HTTPException(404, f"Unknown config key {key!r}.")


def _entry(row: ConfigRow) -> ConfigEntry:
    return ConfigEntry(
        key=row.key,
        value=row.value,
        source=row.source,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        admin_edited_at=row.admin_edited_at,
        invalid_reason=row.invalid_reason,
        json_schema=KEY_SPECS[row.key].json_schema(),
    )


def _db_down(exc: Exception) -> HTTPException:
    log.warning("app_config_db_unavailable", error=type(exc).__name__)
    return HTTPException(503, "The database cannot be reached right now. Try again in a moment.")


@router.get(
    "/admin/config",
    operation_id="listAppConfig",
    summary="Admin only: every app_config key with its source and last change",
    response_model=ConfigKeyList,
    responses=_ADMIN_ERRORS,
    tags=["admin"],
)
async def list_config(request: Request, admin: AdminUser) -> ConfigKeyList:
    _hit_admin_limit(request, admin.user_id)
    rows = await _service(request).list_rows()
    return ConfigKeyList(
        items=[
            ConfigKeySummary(
                key=r.key,
                description=KEY_SPECS[r.key].description,
                public=KEY_SPECS[r.key].public,
                source=r.source,
                source_file=KEY_SPECS[r.key].source_file,
                updated_at=r.updated_at,
                updated_by=r.updated_by,
                invalid_reason=r.invalid_reason,
            )
            for r in rows
        ]
    )


@router.get(
    "/admin/config/{key}",
    operation_id="getAppConfig",
    summary="Admin only: one app_config value with its JSON Schema",
    response_model=ConfigEntry,
    responses=_ADMIN_ERRORS,
    tags=["admin"],
)
async def get_config(request: Request, admin: AdminUser, key: ConfigKey) -> ConfigEntry:
    _hit_admin_limit(request, admin.user_id)
    _known(key)
    return _entry(await _service(request).stored_row(key))


@router.put(
    "/admin/config/{key}",
    operation_id="putAppConfig",
    summary="Admin only: replace one app_config value (validated; the old value goes to history)",
    response_model=ConfigEntry,
    responses={
        **_ADMIN_ERRORS,
        409: {"model": ErrorResponse, "description": "The value changed since you read it"},
        413: {"model": ErrorResponse, "description": "The request body is larger than 64 KB"},
        422: {"model": ErrorResponse, "description": "The value is not valid for this key"},
    },
    tags=["admin"],
    dependencies=[Depends(_body_within_limit)],
)
async def put_config(request: Request, admin: AdminUser, key: ConfigKey, body: ConfigUpdate) -> Any:
    _hit_admin_limit(request, admin.user_id)
    _known(key)
    try:
        row = await _service(request).put(
            key,
            body.value,
            expected_updated_at=body.expected_updated_at,
            user_id=admin.user_id,
            admin_edited=body.admin_edited,
        )
    except ValidationError as exc:
        details = [
            FieldError(
                loc=["body", "value", *(p for p in e["loc"] if isinstance(p, str | int))], msg=e["msg"], type=e["type"]
            )
            for e in exc.errors()
        ]
        return error_response(422, f"The value is not valid for {key}.", "validation_error", details=details[:50])
    except CrossKeyInvalid as exc:
        return error_response(
            422,
            str(exc),
            "validation_error",
            details=[FieldError(loc=["body", "value"], msg=str(exc), type="value_error")],
        )
    except StaleWrite as exc:
        raise HTTPException(409, "This setting was changed by someone else. Reload it and try again.") from exc
    except UnknownConfigKey as exc:
        raise HTTPException(404, f"Unknown config key {key!r}.") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise _db_down(exc) from exc
    return _entry(row)


@router.get(
    "/admin/config/{key}/history",
    operation_id="getAppConfigHistory",
    summary="Admin only: previous values of one app_config key, newest first",
    response_model=ConfigHistory,
    responses=_ADMIN_ERRORS,
    tags=["admin"],
)
async def config_history(
    request: Request,
    admin: AdminUser,
    key: ConfigKey,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ConfigHistory:
    _hit_admin_limit(request, admin.user_id)
    _known(key)
    try:
        rows: list[HistoryRow] = await _service(request).history(key, limit=limit)
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise _db_down(exc) from exc
    return ConfigHistory(
        key=key,
        items=[
            ConfigHistoryItem(
                id=r.id,
                value=r.value,
                updated_by=r.updated_by,
                updated_at=r.updated_at,
                op=r.op,
                replaced_at=r.replaced_at,
            )
            for r in rows
        ],
    )


def install_app_config(app: Any, settings: Settings) -> AppConfigService:
    """Attach the service (app.state.app_config) and the routes. The app lifespan calls start()/stop()."""
    svc = AppConfigService(app.state.db, settings.config_dir)
    app.state.app_config = svc
    app.include_router(router)
    return svc
