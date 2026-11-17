"""Send one Web Push message (`pywebpush`, VAPID). The transport is injectable so tests never touch a network.

Payloads are small JSON objects: a `ui_copy` key and the numbers it needs (`k`, `n`), the already worded `title` and
`body` (rendered from the same ui_copy templates), a deep link `url` and a `tag`. They never carry note text,
question content or names.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp
from pywebpush import WebPushException, webpush_async

from ..copy import render
from ..logs import get_logger
from ..settings import Settings

log = get_logger("naadi.push")
TTL_SECONDS = 6 * 3600  # a reminder that arrives after lunch is noise: the push service may drop it by then
MAX_PAYLOAD_BYTES = 2048  # well under the 4 KB limit of the push services
Outcome = Literal["ok", "gone", "failed"]
Transport = Callable[..., Awaitable[Any]]


class PushNotConfigured(RuntimeError):
    """The owner has not provided the VAPID keys (the app is on a private Space, or the keys are not set yet)."""


@dataclass(frozen=True, slots=True)
class Payload:
    key: str  # ui_copy key of the body template
    numbers: Mapping[str, int | float]
    url: str  # in-app path the click opens
    tag: str  # a later push with the same tag replaces the earlier one

    def to_json(self, copy: Mapping[str, str]) -> str:
        values = dict(self.numbers)
        out = {
            "k": self.key,
            "n": values,
            "title": render(copy, "push.title"),
            "body": render(copy, self.key, values),
            "url": self.url,
            "tag": self.tag,
        }
        text = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
        if len(text.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("push payload too large")
        return text


def classify(status: int | None) -> Outcome:
    """404/410: the subscription is gone for good (the user cleared it or uninstalled): delete it. Anything else
    that is not a success is a transient failure."""
    if status is None:
        return "failed"
    if 200 <= status < 300:
        return "ok"
    if status in (404, 410):
        return "gone"
    return "failed"


async def send(
    subscription: Mapping[str, Any],
    data: str,
    settings: Settings,
    *,
    transport: Transport | None = None,
) -> Outcome:
    """Deliver `data` to one stored subscription row (`endpoint`, `p256dh`, `auth`)."""
    if not settings.push_configured or settings.vapid_private_key is None:
        raise PushNotConfigured("VAPID keys are not set")
    send_fn: Transport = transport or webpush_async
    info = {
        "endpoint": subscription["endpoint"],
        "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
    }
    try:
        resp = await send_fn(
            info,
            data=data,
            vapid_private_key=settings.vapid_private_key.get_secret_value(),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=TTL_SECONDS,
            timeout=10,
        )
    except WebPushException as exc:
        outcome = classify(exc.status_code)
        log.info("push_rejected", status=exc.status_code, outcome=outcome)  # never the endpoint: it is a credential
        return outcome
    except (OSError, TimeoutError, aiohttp.ClientError) as exc:
        log.info("push_unreachable", error=type(exc).__name__)
        return "failed"
    status = getattr(resp, "status", None)
    return classify(status if isinstance(status, int) else 201)
