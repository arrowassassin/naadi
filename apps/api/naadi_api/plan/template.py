"""Template rows → generator input, from the database (`plan_template_days`) or the seed's CSV parser."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .generator import TemplateDay

COLUMNS = (
    "day, phase, chapter_ref, chapter_ids, topic, chapter_questions, qbank, self_assessment, admin_task, "
    "extra_resource, target_hours"
)


def template_day(row: Mapping[str, Any] | Any) -> TemplateDay:
    def get(k: str) -> Any:
        return row[k] if isinstance(row, Mapping) else getattr(row, k)

    hours = get("target_hours")
    return TemplateDay(
        day=int(get("day")),
        phase=get("phase"),
        chapter_ref=get("chapter_ref"),
        chapter_ids=tuple(get("chapter_ids") or ()),
        topic=get("topic"),
        chapter_questions=get("chapter_questions"),
        qbank=get("qbank"),
        self_assessment=get("self_assessment"),
        admin_task=get("admin_task"),
        extra_resource=get("extra_resource"),
        target_hours=float(hours) if hours is not None else None,
    )


def template_days(rows: Iterable[Mapping[str, Any] | Any]) -> list[TemplateDay]:
    return sorted((template_day(r) for r in rows), key=lambda t: t.day)
