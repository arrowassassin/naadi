"""Web Push routes: the public key, this student's subscriptions and a test notification.

Reminders need the owner's VAPID keys (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`) and, on iPad, an
installed PWA on a public address. Until the keys are set `GET /api/push/key` says `configured: false` and the
Settings screen shows why instead of a switch.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response

from ..auth import CurrentUser
from ..auth_limits import RateLimiter
from ..config_api.service import AppConfigService
from ..db import Database
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from ..plan.planner_models import (
    PushKeyOut,
    PushSubscriptionIn,
    PushSubscriptionOut,
    PushSubscriptionsOut,
    PushTestOut,
)
from ..plan.planner_routes import limited
from ..plan.store import rows
from ..qbank.routes import as_service
from ..settings import Settings
from .sender import Payload, PushNotConfigured, Transport, send

log = get_logger("naadi.push")
router = APIRouter(prefix="/api/push", tags=["push"])
MAX_SUBSCRIPTIONS = 10  # devices per student

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse, "description": "Invalid inputs"}}
_NOT_CONFIGURED: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Push is not set up on this server (no VAPID keys)"}
}
SUB_COLS = "id, ua_label, created_at, last_ok_at"


def ua_label(user_agent: str) -> str | None:
    """A coarse device label ("iPad · Safari") from the User-Agent; the header itself is never stored."""
    ua = user_agent or ""
    if "iPad" in ua:
        device = "iPad"
    elif "iPhone" in ua:
        device = "iPhone"
    elif "Android" in ua:
        device = "Android"
    elif "Macintosh" in ua:
        device = "Mac"
    elif "Windows" in ua:
        device = "Windows"
    elif "Linux" in ua:
        device = "Linux"
    else:
        return None
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        return device
    return f"{device} · {browser}"


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


@router.get(
    "/key",
    operation_id="getPushKey",
    summary="The application server key the browser subscribes with (and whether push is set up at all)",
    response_model=PushKeyOut,
    responses=_ERRORS,
)
async def get_key(request: Request, user: Annotated[CurrentUser, limited("planner_read")]) -> PushKeyOut:
    s = _settings(request)
    return PushKeyOut(configured=s.push_configured, public_key=s.vapid_public_key if s.push_configured else None)


@router.get(
    "/subscriptions",
    operation_id="listPushSubscriptions",
    summary="The devices that receive reminders",
    response_model=PushSubscriptionsOut,
    responses=_ERRORS,
)
async def list_subscriptions(
    request: Request, user: Annotated[CurrentUser, limited("planner_read")]
) -> PushSubscriptionsOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await rows(
            conn,
            f"select {SUB_COLS} from public.push_subscriptions where user_id = %s order by created_at",  # noqa: S608
            (user.user_id,),
        )

    found = await as_service(_db(request), run)
    return PushSubscriptionsOut(
        configured=_settings(request).push_configured,
        subscriptions=[PushSubscriptionOut.model_validate(r) for r in found],
    )


@router.post(
    "/subscriptions",
    operation_id="createPushSubscription",
    summary="Register this browser for reminders (the same endpoint again updates it)",
    response_model=PushSubscriptionOut,
    status_code=201,
    responses={**_ERRORS, **_INVALID, **_NOT_CONFIGURED},
)
async def create_subscription(
    request: Request, body: PushSubscriptionIn, user: Annotated[CurrentUser, limited("planner_write")]
) -> PushSubscriptionOut | JSONResponse:
    if not _settings(request).push_configured:
        return error_response(409, "Reminders are not set up on this server.", "push_not_configured")
    label = ua_label(request.headers.get("user-agent", ""))

    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]] | None:
        await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"push:{user.user_id}",))
        mine = await rows(
            conn,
            "select count(*) filter (where endpoint <> %s) as others from public.push_subscriptions where user_id = %s",
            (body.endpoint, user.user_id),
        )
        if mine[0]["others"] >= MAX_SUBSCRIPTIONS:
            return None
        # One row per endpoint: a device that is signed in as someone else now belongs to this student.
        return await rows(
            conn,
            "insert into public.push_subscriptions (user_id, endpoint, p256dh, auth, ua_label) values (%s,%s,%s,%s,%s)"  # noqa: S608
            " on conflict (endpoint) do update set user_id = excluded.user_id, p256dh = excluded.p256dh,"
            " auth = excluded.auth, ua_label = excluded.ua_label, failures = 0, last_sent_on = null, sent_today = 0"
            # Knowing an endpoint is not enough to take it over: the same student, or the same browser keys.
            " where push_subscriptions.user_id = excluded.user_id"
            " or (push_subscriptions.p256dh = excluded.p256dh and push_subscriptions.auth = excluded.auth)"
            f" returning {SUB_COLS}",
            (user.user_id, body.endpoint, body.keys.p256dh, body.keys.auth, label),
        )

    made = await as_service(_db(request), run)
    if made is None:
        return error_response(422, "Too many devices are registered.", "too_many_subscriptions")
    if not made:
        return error_response(409, "This device belongs to another account.", "endpoint_in_use")
    return PushSubscriptionOut.model_validate(made[0])


@router.delete(
    "/subscriptions/{subscription_id}",
    operation_id="deletePushSubscription",
    summary="Stop reminders on one device",
    status_code=204,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def delete_subscription(
    request: Request,
    subscription_id: Annotated[uuid.UUID, Path()],
    user: Annotated[CurrentUser, limited("planner_write")],
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await rows(
            conn,
            "delete from public.push_subscriptions where id = %s and user_id = %s returning id",
            (subscription_id, user.user_id),
        )

    if not await as_service(_db(request), run):
        return error_response(404, "This device is not registered.", "not_found")
    return Response(status_code=204)


async def deliver(
    db: Database,
    settings: Settings,
    copy: dict[str, str],
    user_id: uuid.UUID,
    payload: Payload,
    *,
    transport: Transport | None = None,
) -> PushTestOut:
    """Send one payload to every device of one student; 404/410 subscriptions are deleted."""

    async def load(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await rows(
            conn,
            "select id, endpoint, p256dh, auth from public.push_subscriptions where user_id = %s order by created_at",
            (user_id,),
        )

    subs = await as_service(db, load)
    data = payload.to_json(copy)
    sent = removed = failed = 0
    results: list[tuple[uuid.UUID, str]] = []
    for sub in subs:
        results.append((sub["id"], await send(sub, data, settings, transport=transport)))

    async def record(conn: psycopg.AsyncConnection[Any]) -> None:
        for sid, outcome in results:
            if outcome == "ok":
                await conn.execute(
                    "update public.push_subscriptions set last_ok_at = now(), failures = 0 where id = %s", (sid,)
                )
            elif outcome == "gone":
                await conn.execute("delete from public.push_subscriptions where id = %s", (sid,))
            else:
                await conn.execute("update public.push_subscriptions set failures = failures + 1 where id = %s", (sid,))

    await as_service(db, record)
    for _, outcome in results:
        sent += outcome == "ok"
        removed += outcome == "gone"
        failed += outcome == "failed"
    return PushTestOut(sent=sent, removed=removed, failed=failed)


@router.post(
    "/test",
    operation_id="sendTestPush",
    summary="Send a test notification to the student's devices",
    response_model=PushTestOut,
    responses={**_ERRORS, **_NOT_CONFIGURED},
)
async def send_test(request: Request, user: Annotated[CurrentUser, limited("push_test")]) -> PushTestOut | JSONResponse:
    settings = _settings(request)
    if not settings.push_configured:
        return error_response(409, "Reminders are not set up on this server.", "push_not_configured")
    config: AppConfigService = request.app.state.app_config
    copy: dict[str, str] = await config.get("ui_copy")
    transport: Transport | None = getattr(request.app.state, "push_transport", None)
    try:
        return await deliver(
            _db(request),
            settings,
            copy,
            user.user_id,
            Payload("push.test_body", {}, "/plan", "naadi-test"),
            transport=transport,
        )
    except PushNotConfigured:
        return error_response(409, "Reminders are not set up on this server.", "push_not_configured")


def default_limiters() -> dict[str, RateLimiter]:
    return {"push_test": RateLimiter(3, 60.0)}


def install_push_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)
