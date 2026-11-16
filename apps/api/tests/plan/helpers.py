from __future__ import annotations

import datetime as dt
import json
from functools import cache
from pathlib import Path

from naadi_api.plan.generator import PlanInputs, TemplateDay
from naadi_api.plan.template import template_days
from naadi_api.seed.data import parse_plan_csv

DATA = Path(__file__).resolve().parents[4] / "data"
CSV = DATA / "study_plan_step1_60d.csv"

REFERENCE = PlanInputs(
    start=dt.date(2026, 11, 16),
    exam_date=dt.date(2027, 1, 15),
    study_days=frozenset(range(1, 8)),
    daily_hours=10,
)


@cache
def template() -> tuple[TemplateDay, ...]:
    chapters = {c["id"] for c in json.loads((DATA / "chapters.json").read_text(encoding="utf-8"))}
    plan = parse_plan_csv(CSV, template_id="step1-60d", exam="step1", title="Step 1 in 60 days", chapter_ids=chapters)
    return tuple(template_days(plan.days))
