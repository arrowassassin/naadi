"""`get_analytics`: typed queries over the student's own graded work (never model-written SQL).

Each metric maps to one function in `naadi_api/analytics` (S09/S11), run as the student (RLS) and filtering on
their id. Only graded attempts count, so a timed block in progress shows nothing. Rows come back as a table and,
where it reads well, a chart block (in-house SVG on the client, with a data-table fallback).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...analytics import queries
from ...analytics.student import cards_due
from .registry import ToolContext, ToolResult, register_tool

Metric = Literal["accuracy", "time_per_question", "score_trend", "cards_due"]
By = Literal["system", "discipline", "competency", "clinical_science"]
Period = Literal["7d", "14d", "30d", "90d", "all"]
PERIOD_DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}


class AnalyticsArgs(BaseModel):
    metric: Metric = Field(description="accuracy, time_per_question, score_trend or cards_due")
    by: By = Field(default="system", description="Grouping for accuracy")
    period: Period = Field(default="30d")


def _since(period: Period) -> dt.datetime | None:
    days = PERIOD_DAYS.get(period)
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days) if days else None


async def _exam(conn: Any) -> str:
    cur = await conn.execute("select exam_target from public.profiles where user_id = (select auth.uid())")
    row = await cur.fetchone()
    return str(row["exam_target"]) if row else "step1"


async def get_analytics(ctx: ToolContext, args: AnalyticsArgs) -> ToolResult:
    """The student's own study numbers: accuracy by system/discipline/competency, time per question, the score
    trend of finished timed blocks, or flashcards due. Returns rows the student sees as a chart and a table."""
    since = _since(args.period)
    async with ctx.user_tx() as conn:
        if args.metric == "accuracy":
            exam = await _exam(conn)
            rows = await queries.accuracy_by_node(conn, ctx.user_id, exam=exam, kinds=(args.by,), since=since)
            data = [
                {"label": r.label, "percent": r.percent, "attempts": r.attempts, "correct": r.correct}
                for r in rows
                if r.attempts > 0
            ]
            if not data:
                return ToolResult(content="No graded answers in this period yet.")
            lines = [f"{d['label']}: {d['percent']}% of {d['attempts']}" for d in data]
            table = {
                "columns": [
                    {"key": "label", "label_key": f"agent.analytics.{args.by}", "label": None},
                    {"key": "percent", "label_key": "agent.analytics.percent", "label": None},
                    {"key": "attempts", "label_key": "agent.analytics.attempts", "label": None},
                ],
                "rows": data,
            }
            chart = {
                "chart": "bar",
                "x": "label",
                "series": [{"key": "percent", "label_key": "agent.analytics.percent", "label": None}],
                "rows": data,
                "unit": "percent",
            }
            return ToolResult(
                content="Accuracy by " + args.by + ":\n" + "\n".join(lines),
                blocks=[{"type": "chart", "data": chart}, {"type": "table", "data": table}],
            )
        if args.metric == "time_per_question":
            t = await queries.time_per_question(conn, ctx.user_id, since=since)
            if not t.answered:
                return ToolResult(content="No timed answers in this period yet.")
            row = {"answered": t.answered, "mean_s": t.mean_s, "median_s": t.median_s}
            table = {
                "columns": [
                    {"key": "answered", "label_key": "agent.analytics.answered", "label": None},
                    {"key": "mean_s", "label_key": "agent.analytics.mean_s", "label": None},
                    {"key": "median_s", "label_key": "agent.analytics.median_s", "label": None},
                ],
                "rows": [row],
            }
            return ToolResult(
                content=f"Timed answers: {t.answered}; mean {t.mean_s}s; median {t.median_s}s per question.",
                blocks=[{"type": "table", "data": table}],
            )
        if args.metric == "score_trend":
            trend = await queries.score_trend(conn, ctx.user_id, until=None)
            if not trend:
                return ToolResult(content="No finished timed blocks yet.")
            data = [{"date": r.finished_at.date().isoformat(), "score": round(r.score, 1)} for r in trend]
            chart = {
                "chart": "line",
                "x": "date",
                "series": [{"key": "score", "label_key": "agent.analytics.score", "label": None}],
                "rows": data,
                "unit": "percent",
            }
            table = {
                "columns": [
                    {"key": "date", "label_key": "agent.analytics.date", "label": None},
                    {"key": "score", "label_key": "agent.analytics.score", "label": None},
                ],
                "rows": data,
            }
            return ToolResult(
                content="Scores of finished timed blocks, oldest first: "
                + ", ".join(f"{d['date']} {d['score']}%" for d in data),
                blocks=[{"type": "chart", "data": chart}, {"type": "table", "data": table}],
            )
        due = await cards_due(conn, now=dt.datetime.now(dt.UTC))
        table = {
            "columns": [
                {"key": "due", "label_key": "agent.analytics.cards_due", "label": None},
                {"key": "reps", "label_key": "agent.analytics.reps", "label": None},
                {"key": "lapses", "label_key": "agent.analytics.lapses", "label": None},
            ],
            "rows": [due],
        }
        return ToolResult(
            content=f"Flashcards due now: {due['due']} (reviews {due['reps']}, lapses {due['lapses']}).",
            blocks=[{"type": "table", "data": table}],
        )


register_tool("get_analytics", get_analytics, writes=False)
