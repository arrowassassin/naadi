"""Database side of study plans: read the template, write a generated plan (days + items), read it back.

Plans, plan days and coach suggestions are written with the pool's privileged role for the verified caller
only (clients may change only their status, see migration 0013); every statement names the user explicitly.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .generator import GENERATOR_VERSION, PlanDay, PlanInputs, TemplateDay
from .items import ItemMinutes, plan_items
from .template import COLUMNS, template_days

Row = dict[str, Any]


async def rows(conn: psycopg.AsyncConnection[Any], query: str, params: Any = ()) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def load_template(conn: psycopg.AsyncConnection[Any], template_id: str) -> list[TemplateDay]:
    found = await rows(
        conn,
        f"select {COLUMNS} from public.plan_template_days where template_id = %s order by day",  # noqa: S608 - constant columns
        (template_id,),
    )
    return template_days(found)


def inputs_json(inputs: PlanInputs, exam: str) -> dict[str, Any]:
    return {
        "exam": exam,
        "start": inputs.start.isoformat(),
        "exam_date": inputs.exam_date.isoformat(),
        "study_days": sorted(inputs.study_days),
        "daily_hours": inputs.daily_hours,
        "reference_hours": inputs.reference_hours,
    }


async def write_plan(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    *,
    template_id: str,
    exam: str,
    inputs: PlanInputs,
    days: Sequence[PlanDay],
    sizes: ItemMinutes,
) -> uuid.UUID:
    """Replace the student's active plan: the old one stays (inactive, for history) and loses its generator
    items from `inputs.start` on; the new plan's days and items are written in one transaction."""
    old = await rows(
        conn, "update public.study_plans set active = false where user_id = %s and active returning id", (user_id,)
    )
    if old:
        await conn.execute(
            "delete from public.plan_items where user_id = %s and plan_id = any(%s) and origin = 'generator'"
            " and day >= %s",
            (user_id, [r["id"] for r in old], inputs.start),
        )
    plan_id = uuid.uuid4()
    await conn.execute(
        "insert into public.study_plans (id, user_id, template_id, inputs, generator_version) values (%s,%s,%s,%s,%s)",
        (plan_id, user_id, template_id, Jsonb(inputs_json(inputs, exam)), GENERATOR_VERSION),
    )
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into public.study_plan_days (user_id, plan_id, day_no, date, weekday, week, phase, chapter_ref,"
            " chapter_ids, topic, chapter_questions, qbank, self_assessment, admin_task, extra_resource, target_hours,"
            " template_day, merged_days, extra) values"
            " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                (
                    user_id,
                    plan_id,
                    d.day_no,
                    d.date,
                    d.weekday,
                    d.week,
                    d.phase,
                    d.chapter_ref,
                    list(d.chapter_ids),
                    d.topic,
                    d.chapter_questions,
                    d.qbank,
                    d.self_assessment,
                    d.admin_task,
                    d.extra_resource,
                    d.target_hours,
                    d.template_day,
                    list(d.merged_days),
                    d.extra,
                )
                for d in days
            ],
        )
        items = plan_items(days, exam, sizes)
        by_no = {d.day_no: d for d in days}
        await cur.executemany(
            "insert into public.plan_items (user_id, day, kind, title, ref_type, ref_id, target_minutes, target_count,"
            " origin, sort, plan_id, plan_day) values (%s,%s,%s,%s,%s,%s,%s,%s,'generator',%s,%s,%s)",
            [
                (
                    user_id,
                    by_no[n].date,
                    i.kind,
                    i.title,
                    i.ref_type,
                    i.ref_id,
                    i.target_minutes,
                    i.target_count,
                    i.sort,
                    plan_id,
                    n,
                )
                for n, its in items.items()
                for i in its
            ],
        )
    return plan_id


async def save_profile(
    conn: psycopg.AsyncConnection[Any],
    *,
    exam: str,
    exam_date: dt.date,
    daily_hours: float,
    study_days: Sequence[int],
    onboarding: dict[str, Any],
) -> Row:
    """As the student (RLS): the profile's plan inputs and `settings.onboarding` (merged one level deep)."""
    await conn.execute(
        "insert into public.profiles (user_id) values ((select auth.uid())) on conflict (user_id) do nothing"
    )
    out = await rows(
        conn,
        "update public.profiles set exam_target = %(exam)s, exam_date = %(date)s, daily_hours = %(hours)s,"
        " study_days = %(days)s::smallint[],"
        " settings = settings || jsonb_build_object('onboarding',"
        "   coalesce(settings->'onboarding', '{}'::jsonb) || %(onb)s::jsonb)"
        " where user_id = (select auth.uid()) returning settings",
        {
            "exam": exam,
            "date": exam_date,
            "hours": daily_hours,
            "days": list(study_days),
            "onb": json.dumps(onboarding, separators=(",", ":")),
        },
    )
    return out[0]


def item_row_out(r: Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "day": r["day"],
        "kind": r["kind"],
        "title": r["title"],
        "ref_type": r["ref_type"],
        "ref_id": r["ref_id"],
        "target_minutes": r["target_minutes"],
        "target_count": r["target_count"],
        "status": r["status"],
        "origin": r["origin"],
        "sort": r["sort"],
        "plan_day": r["plan_day"],
    }


ITEM_COLS = "id, day, kind, title, ref_type, ref_id, target_minutes, target_count, status, origin, sort, plan_day"
DAY_COLS = (
    "day_no, date, weekday, week, phase, chapter_ref, chapter_ids, topic, target_hours::float8 as target_hours,"
    " template_day, merged_days::int[] as merged_days, extra, (self_assessment is not null) as assessment, status"
)


def day_out(d: Row, items: Sequence[Row]) -> dict[str, Any]:
    return {
        **d,
        "chapter_ids": list(d["chapter_ids"]),
        "merged_days": list(d["merged_days"]),
        "items": [item_row_out(i) for i in items],
    }
