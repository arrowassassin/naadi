"""The daily reminder pass (system cron every 15 minutes, graph `planner` mode `reminders`).

For every device with reminders on, once the student's local clock passes `planner.reminder_local_time` (and for a
few hours after, in case a run was missed), it sends at most `planner.push_max_per_day` messages, once per local day:

1. the reminder: how many items are open today and for about how long;
2. the coach nudge, when items from earlier days are behind: how many, and that a rebalance can fix it.

Each message is a `ui_copy` key plus numbers plus a deep link (see `naadi_api.push.sender`), never note text. A day
with nothing to say sends nothing. 404/410 from a push service deletes the subscription; other failures count up.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..config_api.schemas import PlannerRules
from ..db import Database
from ..logs import get_logger
from ..plan import planner_store as ps
from ..plan.rebalance import MOVABLE_STATUSES
from ..plan.store import rows
from ..settings import Settings
from .sender import Payload, PushNotConfigured, Transport, send

log = get_logger("naadi.push")
WINDOW_HOURS = 4  # after the reminder time a late run still sends; later it would be noise
MAX_DEVICES_PER_PASS = 2000
MAX_FAILURES = 5  # consecutive transient failures before a device is dropped


@dataclass
class Stats:
    devices: int = 0
    students: int = 0
    sent: int = 0
    removed: int = 0
    failed: int = 0
    quiet: int = 0
    skipped: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "devices": self.devices,
            "students": self.students,
            "sent": self.sent,
            "removed": self.removed,
            "failed": self.failed,
            "quiet": self.quiet,
            "skipped": self.skipped,
        }


def due(local: dt.datetime, reminder_time: str, last_sent_on: dt.date | None) -> bool:
    """Is it time to send to a device: past the reminder time, inside the window, and not yet sent today."""
    hh, mm = (int(x) for x in reminder_time.split(":"))
    start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= local < start + dt.timedelta(hours=WINDOW_HOURS) and last_sent_on != local.date()


def todays_payloads(data: ps.PlanData, rules: PlannerRules, now: dt.datetime) -> list[Payload]:
    """What to say to this student today, most useful first, at most `push_max_per_day` messages."""
    local = ps.local_now(data.profile.get("timezone"), now)
    today = local.date()
    open_today = [i for i in data.items if i["day"] == today and i["status"] in MOVABLE_STATUSES]
    out: list[Payload] = []
    if open_today:
        minutes = sum(i["target_minutes"] or 0 for i in open_today)
        out.append(
            Payload(
                "push.reminder_body",
                {"items": len(open_today), "hours": round(minutes / 60, 1)},
                "/plan",
                f"naadi-reminder-{today.isoformat()}",
            )
        )
    behind = ps.behind_count(data, rules, now)
    if behind:
        out.append(Payload("push.nudge_body", {"behind": behind}, "/plan", f"naadi-nudge-{today.isoformat()}"))
    return out[: rules.push_max_per_day]


async def reminder_pass(
    db: Database,
    settings: Settings,
    rules: PlannerRules,
    copy: dict[str, str],
    *,
    now: dt.datetime | None = None,
    transport: Transport | None = None,
) -> Stats:
    from ..coach.run import service_conn  # the same privileged, per-statement-user connection the coach uses

    stats = Stats()
    if not settings.push_configured:
        stats.skipped = "not_configured"
        return stats
    now = now or dt.datetime.now(dt.UTC)
    async with service_conn(db) as conn:
        devices = await rows(
            conn,
            "select s.id, s.user_id, s.endpoint, s.p256dh, s.auth, s.last_sent_on, p.timezone"
            " from public.push_subscriptions s join public.profiles p on p.user_id = s.user_id"
            " order by s.user_id, s.created_at limit %s",
            (MAX_DEVICES_PER_PASS,),
        )
    stats.devices = len(devices)
    by_user: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for d in devices:
        local = ps.local_now(d["timezone"], now)
        if due(local, rules.reminder_local_time, d["last_sent_on"]):
            by_user[d["user_id"]].append({**d, "local_date": local.date()})
    for user_id, subs in by_user.items():
        stats.students += 1
        async with service_conn(db) as conn:
            data = await ps.load_plan(conn, user_id)
        payloads = todays_payloads(data, rules, now) if data is not None else []
        if not payloads:
            stats.quiet += 1
        results: list[tuple[uuid.UUID, dt.date, int, str]] = []
        for sub in subs:
            outcome = "ok"
            sent_n = 0
            for payload in payloads:
                try:
                    outcome = await send(sub, payload.to_json(copy), settings, transport=transport)
                except PushNotConfigured:
                    stats.skipped = "not_configured"
                    return stats
                if outcome != "ok":
                    break
                sent_n += 1
            results.append((sub["id"], sub["local_date"], sent_n, outcome if payloads else "quiet"))
        async with service_conn(db) as conn:
            for sid, local_date, sent_n, outcome in results:
                if outcome == "gone":
                    await conn.execute("delete from public.push_subscriptions where id = %s", (sid,))
                    stats.removed += 1
                elif outcome == "failed":
                    await conn.execute(
                        "update public.push_subscriptions set failures = failures + 1 where id = %s", (sid,)
                    )
                    await conn.execute(
                        "delete from public.push_subscriptions where id = %s and failures >= %s", (sid, MAX_FAILURES)
                    )
                    stats.failed += 1
                else:
                    await conn.execute(
                        "update public.push_subscriptions set last_sent_on = %s, sent_today = %s,"
                        " last_ok_at = case when %s > 0 then now() else last_ok_at end, failures = 0 where id = %s",
                        (local_date, sent_n, sent_n, sid),
                    )
                    stats.sent += sent_n
    log.info("push_reminders", **stats.as_dict())
    return stats
