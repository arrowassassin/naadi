"""The nightly psychometric pass: first attempts -> `questions.stats` (hidden from clients) with flags.

It only reads `attempts`/`report_issues` and writes `questions.stats`; it never changes a status, and below
`act_min_n` responses a flag is all it can raise (config/authoring_rules.md Part C)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from ..config_api.schemas import AuthoringRules
from ..db import Database
from .psychometrics import FirstAttempt, PsychometricRules, flags_for, item_stats
from .store import service_conn

FIRST_ATTEMPTS = """
select distinct on (a.user_id, a.question_id) a.user_id, a.question_id, a.chosen, a.correct, a.seconds
from public.attempts a join public.questions q on q.id = a.question_id and q.status = 'published'
where a.mode in ('tutor', 'timed') and a.chosen is not null and a.correct is not null
order by a.user_id, a.question_id, a.created_at, a.id
"""


def rules_of(cfg: AuthoringRules) -> PsychometricRules:
    return PsychometricRules(
        min_n=cfg.psychometrics_min_n,
        act_min_n=cfg.act_min_n,
        p_low=cfg.p_low,
        p_high=cfg.p_high,
        pb_min=cfg.pb_min,
        time_factor=cfg.time_factor,
        report_flags=cfg.report_flags,
    )


@dataclass
class PsychStats:
    items: int = 0
    flagged: int = 0
    updated: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"items": self.items, "flagged": self.flagged, "updated": self.updated}


async def run(db: Database, cfg: AuthoringRules) -> PsychStats:
    rules = rules_of(cfg)
    out = PsychStats()
    async with service_conn(db) as conn:
        cur = await conn.execute(FIRST_ATTEMPTS)
        attempts = await cur.fetchall()
        per_user: dict[Any, list[int]] = defaultdict(lambda: [0, 0])
        by_q: dict[str, list[FirstAttempt]] = defaultdict(list)
        for a in attempts:
            tot = per_user[a["user_id"]]
            tot[0] += 1 if a["correct"] else 0
            tot[1] += 1
            by_q[a["question_id"]].append(FirstAttempt(a["user_id"], a["chosen"], bool(a["correct"]), a["seconds"]))
        times = [a["seconds"] for a in attempts if a["seconds"] is not None]
        bank_median = statistics.median(times) if times else None
        cur = await conn.execute(
            "select ref_id, count(*)::int as n from public.report_issues"
            " where ref_type = 'question' and status in ('open', 'triaged') group by ref_id"
        )
        reports = {r["ref_id"]: r["n"] for r in await cur.fetchall()}
        cur = await conn.execute(
            "select id, answer, options, stats from public.questions where status = 'published'"
            " and (id = any(%s) or id = any(%s) or jsonb_array_length(coalesce(stats->'flags', '[]'::jsonb)) > 0)",
            (list(by_q), list(reports)),
        )
        totals = {u: (v[0], v[1]) for u, v in per_user.items()}
        for q in await cur.fetchall():
            labels = [str(o["label"]) for o in q["options"]]
            stats = item_stats(by_q.get(q["id"], []), str(q["answer"]).strip(), labels, totals, reports.get(q["id"], 0))
            stats["flags"] = flags_for(stats, str(q["answer"]).strip(), rules, bank_median)
            out.items += 1
            out.flagged += 1 if stats["flags"] else 0
            before = q["stats"] or {}
            comparable = {k: v for k, v in stats.items() if k != "computedAt"}
            if comparable != {k: v for k, v in before.items() if k != "computedAt"}:
                await conn.execute("update public.questions set stats = %s where id = %s", (Jsonb(stats), q["id"]))
                out.updated += 1
    return out
