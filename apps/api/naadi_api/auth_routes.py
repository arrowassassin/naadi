"""Auth-adjacent API: public config, the signed-in user (`/api/me`), navigation and the admin gate.

User data (`profiles`) is read and written only through `user_tx`, i.e. as the user under RLS.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Annotated, Any, Literal, LiteralString
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import AdminUser, AuthError, AuthUnavailable, CurrentUser, get_account_user, get_current_user
from .auth_db import user_tx
from .auth_limits import RateLimiter
from .config_api.schemas import ExamRules, NavEntry
from .config_api.service import AppConfigService
from .db import Database, DatabaseNotConfigured
from .errors import ErrorResponse
from .ink.models import InkSettings
from .logs import get_logger
from .settings import Settings

log = get_logger("naadi.me")

MAX_SETTINGS_PATCH_BYTES = 16 * 1024
MAX_SETTINGS_TOTAL_BYTES = 64 * 1024
# Short: feature flags, navigation and UI copy are edited at runtime (app_config) and must show up quickly.
PUBLIC_CONFIG_CACHE = "public, max-age=60"

router = APIRouter(prefix="/api")

_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Auth or database unavailable"},
}


# ── Models ──


class PublicConfig(BaseModel):
    """Everything the web app needs before sign-in. Nothing here is secret."""

    auth_configured: bool
    supabase_url: str | None
    supabase_anon_key: str | None = Field(description="The public anon key (safe to expose; RLS protects data)")
    google_oauth_enabled: bool
    app_base_url: str | None = Field(description="Canonical origin used for auth redirects")
    app_version: str
    features: dict[str, bool] = Field(
        description="Feature flags (app_config.feature_flags) resolved for a signed-out visitor, e.g. ink_lab"
    )
    nav: list[NavEntry] = Field(description="Navigation items whose flag is on for a signed-out visitor")
    exam_rules: ExamRules
    ui_copy: dict[str, str] = Field(description="Editable UI strings (app_config.ui_copy); {name} = placeholder")


ExamTarget = Literal["step1", "step2ck", "step3"]


class Profile(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    exam_target: ExamTarget
    exam_date: dt.date | None
    daily_hours: float
    study_days: list[int]
    timezone: str
    pathway_stage: str | None
    settings: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class MeResponse(BaseModel):
    profile: Profile
    email: str | None = Field(description="The signed-in user's own email, from the verified token")
    email_verified: bool
    is_admin: bool
    features: dict[str, bool] = Field(description="Feature flags resolved for this user (admins see admin flags)")


def _valid_timezone(v: str) -> str:
    v = v.strip()
    if not v or len(v) > 64 or v.startswith(("/", ".")) or ".." in v:
        raise ValueError("Use an IANA time zone name such as Asia/Kolkata.")
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use an IANA time zone name such as Asia/Kolkata.") from exc
    return v


class MeUpdate(BaseModel):
    """Only the fields sent are changed. `settings` is merged one level deep; a `null` value removes a key."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    settings: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v or None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _valid_timezone(v)

    @field_validator("settings")
    @classmethod
    def _settings(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        if len(json.dumps(v, separators=(",", ":")).encode()) > MAX_SETTINGS_PATCH_BYTES:
            raise ValueError(f"settings must be at most {MAX_SETTINGS_PATCH_BYTES // 1024} KB")
        if any(not k or len(k) > 64 for k in v):
            raise ValueError("settings keys must be 1-64 characters")
        if v.get("ink") is not None:
            # Settings -> Pencil & ink (segment 12b): replaced whole (the merge is one level deep), so validate whole.
            v = {**v, "ink": InkSettings.model_validate(v["ink"]).model_dump(exclude_none=True)}
        return v


class NavItem(BaseModel):
    id: str
    label: str
    path: str = Field(description="In-app route path")
    icon: str = Field(description="Icon name from the web app's icon set")
    feature: str = Field(description="Feature flag that must be on for the item to be shown")


class NavResponse(BaseModel):
    items: list[NavItem] = Field(description="Enabled items only, in display order")
    features: dict[str, bool] = Field(description="Feature flags resolved for this user")
    source: Literal["app_config", "file_fallback"] = Field(
        description="file_fallback = app_config could not be read; config/app_config.yaml was used (logged)"
    )


class AdminOverview(BaseModel):
    environment: str
    version: str
    admins_configured: int
    profiles: int


# ── Dependencies ──


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def user_limited(bucket: str, *, account: bool = True) -> Any:
    """Dependency factory: authenticate, then count the request against the user's bucket. With `account`
    (the default) the session and account are checked in `auth` too (revoked sessions get 401)."""
    who = get_account_user if account else get_current_user

    # A default value, not an annotation: annotations are strings here and could not see the local `who`.
    async def dep(request: Request, user: CurrentUser = Depends(who)) -> CurrentUser:  # noqa: B008
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def default_rate_limiters() -> dict[str, RateLimiter]:
    return {
        "me_read": RateLimiter(120, 60.0),
        "me_write": RateLimiter(30, 60.0),
        "admin": RateLimiter(60, 60.0),
    }


def _config(request: Request) -> AppConfigService:
    c: AppConfigService = request.app.state.app_config
    return c


# ── Routes ──


@router.get(
    "/public-config",
    operation_id="getPublicConfig",
    summary="Public client configuration (no auth): Supabase URL + anon key, sign-in options, version",
    response_model=PublicConfig,
    tags=["auth"],
)
async def public_config(request: Request) -> JSONResponse:
    s = _settings(request)
    cfg = _config(request)
    body = PublicConfig(
        auth_configured=bool(s.supabase_url and s.supabase_anon_key),
        supabase_url=s.supabase_url,
        supabase_anon_key=s.supabase_anon_key,
        google_oauth_enabled=bool(s.google_oauth_configured and s.supabase_url),
        app_base_url=s.app_base_url.rstrip("/") if s.app_base_url else None,
        app_version=s.app_version,
        features=await cfg.resolved_features(is_admin=False, app_env=s.app_env),
        nav=[NavEntry.model_validate(i) for i in await cfg.nav_for(is_admin=False, app_env=s.app_env)],
        exam_rules=ExamRules.model_validate(await cfg.get("exam_rules")),
        ui_copy=await cfg.get("ui_copy"),
    )
    return JSONResponse(body.model_dump(mode="json"), headers={"cache-control": PUBLIC_CONFIG_CACHE})


_PROFILE_COLS = sql.SQL(
    "user_id, display_name, exam_target, exam_date, daily_hours::float8 as daily_hours, "
    "study_days::int[] as study_days, timezone, pathway_stage, settings, created_at, updated_at"
)

# One round trip: create the row on first sign-in (idempotent), then return it. The CTE's insert is not
# visible to the outer select of the same statement, hence the union.
_ENSURE_SQL = sql.SQL(
    """
with ins as (
  insert into public.profiles (user_id) values ((select auth.uid()))
  on conflict (user_id) do nothing
  returning {cols}
)
select * from ins
union all
select {cols} from public.profiles where user_id = (select auth.uid())
limit 1
"""
).format(cols=_PROFILE_COLS)


async def _ensure_profile(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any]:
    cur = await conn.execute(_ENSURE_SQL)
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("profile upsert returned no row")
    return dict(row)


async def _me(request: Request, user: CurrentUser, row: dict[str, Any]) -> MeResponse:
    features = await _config(request).resolved_features(is_admin=user.is_admin, app_env=_settings(request).app_env)
    return MeResponse(
        profile=Profile.model_validate(row),
        email=user.email,
        email_verified=user.email_verified,
        is_admin=user.is_admin,
        features=features,
    )


async def _as_user(db: Database, user: CurrentUser, fn: Any) -> Any:
    try:
        async with user_tx(db, user) as conn:
            return await fn(conn)
    except DatabaseNotConfigured as exc:
        raise AuthUnavailable("Your profile cannot be loaded right now. Try again in a moment.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        # A valid token for a user who no longer exists (deleted account).
        raise AuthError("account_missing", "This account no longer exists. Sign in again.") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        # Includes psycopg_pool's PoolTimeout / PoolClosed (both OperationalError subclasses).
        log.warning("profile_db_unavailable", error=type(exc).__name__)
        raise AuthUnavailable("Your profile cannot be loaded right now. Try again in a moment.") from exc


@router.get(
    "/me",
    operation_id="getMe",
    summary="The signed-in user's profile (created on first call), admin flag and feature flags",
    response_model=MeResponse,
    responses=_AUTH_ERRORS,
    tags=["auth"],
)
async def get_me(request: Request, user: Annotated[CurrentUser, user_limited("me_read")]) -> MeResponse:
    row = await _as_user(_db(request), user, _ensure_profile)
    return await _me(request, user, row)


@router.patch(
    "/me",
    operation_id="updateMe",
    summary="Update display name, time zone and settings (as the user, under RLS)",
    response_model=MeResponse,
    responses={**_AUTH_ERRORS, 422: {"model": ErrorResponse, "description": "Invalid fields"}},
    tags=["auth"],
)
async def update_me(
    request: Request,
    patch: MeUpdate,
    user: Annotated[CurrentUser, user_limited("me_write")],
) -> MeResponse:
    sent = patch.model_fields_set
    sets: list[LiteralString] = []
    params: dict[str, Any] = {}
    if "display_name" in sent:
        sets.append("display_name = %(display_name)s")
        params["display_name"] = patch.display_name
    if "timezone" in sent:
        if patch.timezone is None:
            raise _validation("timezone", "A time zone is required.")
        sets.append("timezone = %(timezone)s")
        params["timezone"] = patch.timezone
    if "settings" in sent and patch.settings is not None:
        keep = {k: v for k, v in patch.settings.items() if v is not None}
        drop = [k for k, v in patch.settings.items() if v is None]
        sets.append("settings = (settings || %(merge)s::jsonb) - %(drop)s::text[]")
        params["merge"] = json.dumps(keep, separators=(",", ":"))
        params["drop"] = drop

    async def run(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any]:
        row = await _ensure_profile(conn)
        if not sets:
            return row
        # `sets` holds only the fixed clauses above; values are always bind parameters.
        stmt = sql.SQL(
            "update public.profiles set {sets}, updated_at = now() where user_id = (select auth.uid()) "
            "returning {cols}, octet_length(settings::text) as settings_bytes"
        ).format(sets=sql.SQL(", ").join(sql.SQL(c) for c in sets), cols=_PROFILE_COLS)
        cur = await conn.execute(stmt, params)
        updated = await cur.fetchone()
        if updated is None:
            raise RuntimeError("profile update matched no row")
        out = dict(updated)
        if int(out.pop("settings_bytes")) > MAX_SETTINGS_TOTAL_BYTES:
            raise _validation("settings", f"settings may hold at most {MAX_SETTINGS_TOTAL_BYTES // 1024} KB in total")
        return out

    try:
        row = await _as_user(_db(request), user, run)
    except psycopg.errors.CheckViolation as exc:
        raise _validation("body", "One of the values is out of range.") from exc
    log.info("profile_updated", fields=sorted(sent))
    return await _me(request, user, row)


class _FieldInvalid(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _validation(field: str, message: str) -> _FieldInvalid:
    return _FieldInvalid(field, message)


async def _field_invalid_handler(_: Request, exc: Exception) -> JSONResponse:
    from .errors import FieldError, error_response

    assert isinstance(exc, _FieldInvalid)
    return error_response(
        422,
        "The request was not valid.",
        "validation_error",
        details=[FieldError(loc=["body", exc.field], msg=exc.message, type="value_error")],
    )


@router.get(
    "/nav",
    operation_id="getNav",
    summary="Navigation for the signed-in user: enabled items only, plus shell feature flags",
    response_model=NavResponse,
    responses=_AUTH_ERRORS,
    tags=["auth"],
)
async def get_nav(
    request: Request, user: Annotated[CurrentUser, user_limited("me_read", account=False)]
) -> NavResponse:
    # Token-only (no account lookup, so the shell's most frequent call stays cheap): the admin decision lives in
    # `auth` and is not made here, so flags and items with audience "admins" resolve as for a student. Admin
    # flags are served by /api/me (account-checked). An admin-only nav item would need account=True here.
    cfg = _config(request)
    env = _settings(request).app_env
    rows = (await cfg.row("feature_flags"), await cfg.row("ui.nav"))
    return NavResponse(
        items=[NavItem.model_validate(i) for i in await cfg.nav_for(is_admin=False, app_env=env)],
        features=await cfg.resolved_features(is_admin=False, app_env=env),
        source="app_config" if all(r.source == "db" for r in rows) else "file_fallback",
    )


@router.get(
    "/admin/overview",
    operation_id="getAdminOverview",
    summary="Admin only: environment, version, number of admins configured and of profiles",
    response_model=AdminOverview,
    responses={**_AUTH_ERRORS, 403: {"model": ErrorResponse, "description": "Not an admin"}},
    tags=["admin"],
)
async def admin_overview(request: Request, admin: AdminUser) -> AdminOverview:
    request.app.state.rate_limiters["admin"].hit(str(admin.user_id))
    s = _settings(request)
    db = _db(request)
    count = 0
    if db.configured:
        if db.pool is None:
            await db.open()
        assert db.pool is not None
        try:
            # Aggregate over all users, admin-gated above: the API's own role, no per-row data leaves the DB.
            async with db.pool.connection(timeout=5.0) as conn:
                cur = await conn.execute("select count(*) from public.profiles")
                row = await cur.fetchone()
                count = int(row[0]) if row else 0
        except (psycopg.OperationalError, TimeoutError) as exc:
            log.warning("admin_db_unavailable", error=type(exc).__name__)
            raise AuthUnavailable("The database cannot be reached right now. Try again in a moment.") from exc
    return AdminOverview(
        environment=s.app_env,
        version=s.app_version,
        admins_configured=len(request.app.state.admin_emails),
        profiles=count,
    )


def install_auth_routes(app: Any) -> None:
    from .auth_limits import RateLimited, rate_limited_handler

    app.state.rate_limiters = default_rate_limiters()
    app.add_exception_handler(RateLimited, rate_limited_handler)
    app.add_exception_handler(_FieldInvalid, _field_invalid_handler)
    app.include_router(router)


__all__ = ["install_auth_routes", "router"]
