"""Creating a timed/exam block from code (contract S11 <-> S09, ledger 20261116T212256): the onboarding
diagnostic chooses its own questions and runs them in the timed player at `/practice/blocks/{id}/1`."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..auth import CurrentUser
from ..db import Database
from . import exam
from .models import BlockConfig, BlockDetail
from .routes import _insert_block, _timed_layout


async def create_timed_block(
    db: Database,
    user: CurrentUser,
    cfg: BlockConfig,
    question_ids: list[str],
    *,
    rules: dict[str, Any],
    sections: int = 1,
    now: dt.datetime | None = None,
) -> BlockDetail:
    """A timed (or exam) block over `question_ids`, split into `sections` equal sections, each timed at
    exam_rules.modes[mode].seconds_per_question per question; the caller has checked the questions are visible
    to the user. Raises ValueError for a tutor config or a split that does not divide the questions."""
    if not exam.is_timed(cfg.mode):
        raise ValueError("create_timed_block needs mode timed or exam")
    if sections < 1 or len(question_ids) % sections:
        raise ValueError("questions must split evenly into sections")
    layout, break_s = _timed_layout(rules, cfg.exam, cfg.mode, len(question_ids) // sections, sections)
    full = cfg.model_copy(update={"sections": layout, "break_s": break_s})
    return await _insert_block(db, user, full, question_ids, rules=rules, now=now or exam.default_now())


__all__ = ["create_timed_block"]
