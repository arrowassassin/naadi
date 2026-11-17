"""Offline manifest pieces without a database: media rules, planned chapters, byte sizes, the shared question
serializer's reveal gate."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest

from naadi_api.courses import ChapterDetail, Coverage, MediaOut
from naadi_api.offline.models import OfflineDay, OfflinePlan
from naadi_api.offline.routes import json_bytes, offline_media, part_size, planned_chapters
from naadi_api.plan.models import PlanDayMeta, PlanItemOut
from naadi_api.qbank.models import Explanation
from naadi_api.qbank.routes import question_view, tutor_reveal

HIDDEN = ("answer", "answer_html", "explanation_html", "why_not_html", "educational_objective")


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def media(mid: str, kind: str, url: str | None = "https://x.test/f", height: int | None = 100) -> MediaOut:
    return MediaOut(
        id=mid,
        kind=kind,  # type: ignore[arg-type]
        url=url,
        title=None,
        caption=None,
        attribution=None,
        width=100,
        height=height,
        youtube_id="abc" if kind == "video_youtube" else None,
        duration_s=None,
        embeddable=None,
        source=None,
    )


def chapter(cid: str, items: list[MediaOut]) -> ChapterDetail:
    return ChapterDetail(
        id=cid,
        exam="step1",
        kind="chapter",
        title="T",
        meta=None,
        number=1,
        chapter_count=1,
        systems=[],
        coverage=Coverage(sections=0, reading_minutes=0, figures=0, videos=0, questions=0, cards=0),
        sections=[],
        media={m.id: m for m in items},
        questions={},
        next_chapter=None,
    )


def test_media_rules_take_figures_and_small_own_videos_and_never_youtube() -> None:
    ch = chapter(
        "c1",
        [
            media("fig", "svg"),
            media("img", "image"),
            media("yt", "video_youtube"),
            media("v720", "video_own", height=720),
            media("v1080", "video_own", height=1080),
            media("v4k", "video_own", height=2160),
            media("vnone", "video_own", height=None),
            media("pdf", "pdf"),
            media("nourl", "svg", url=None),
            media("signed", "image"),
        ],
    )
    figures, videos, skipped = offline_media([ch, chapter("c2", [media("fig", "svg")])], {"fig", "img"})
    assert [f.media_id for f in figures] == ["fig", "img"]
    assert [(v.media_id, v.height) for v in videos] == [("v720", 720), ("v1080", 1080)]
    assert {s.media_id: s.reason for s in skipped} == {
        "yt": "youtube",
        "v4k": "over_1080p",
        "vnone": "size_unknown",
        "pdf": "not_offline",
        "nourl": "no_url",
        "signed": "not_public",
    }


def item(day: dt.date, ref_type: str | None, ref_id: str | None) -> PlanItemOut:
    return PlanItemOut(
        id=uuid.uuid4(),
        day=day,
        kind="read",
        title="t",
        ref_type=ref_type,
        ref_id=ref_id,
        target_minutes=None,
        target_count=None,
        status="planned",
        origin="generator",
        sort=0,
        plan_day=1,
    )


def test_planned_chapters_follow_the_plan_order_without_duplicates() -> None:
    d0 = dt.date(2026, 11, 16)
    meta = PlanDayMeta(
        day_no=1,
        date=d0,
        weekday="Mon",
        week=1,
        phase=None,
        chapter_ref=None,
        chapter_ids=["ch2", "ch3"],
        topic=None,
        target_hours=None,
        template_day=1,
        merged_days=[],
        extra=False,
        assessment=False,
        status="",
    )
    plan = OfflinePlan(
        plan_id=None,
        days=[
            OfflineDay(date=d0, plan_day=meta, items=[item(d0, "chapter", "ch1"), item(d0, "qbank", "step1")]),
            OfflineDay(date=d0 + dt.timedelta(days=1), plan_day=None, items=[item(d0, "chapter", "ch2")]),
        ],
    )
    assert planned_chapters(plan) == ["ch1", "ch2", "ch3"]


def test_bytes_are_the_json_length_of_the_part() -> None:
    chapters = [chapter("c1", [media("fig", "svg")])]
    raw = json.dumps([c.model_dump(mode="json") for c in chapters], separators=(",", ":"), ensure_ascii=False)
    s = part_size("chapters", 1, chapters)
    assert s.bytes == len(raw.encode()) == json_bytes(chapters) > 0 and s.count == 1
    assert part_size("chapters", 1, chapters, chapters).bytes == 2 * s.bytes
    # non-ASCII text counts in UTF-8 bytes
    assert json_bytes(chapter("cé", [])) == json_bytes(chapter("ce", [])) + 1


BLOCK = {
    "id": uuid.uuid4(),
    "question_ids": ["q1", "q2"],
    "config": {
        "exam": "step1",
        "mode": "tutor",
        "status": "all",
        "difficulty": None,
        "systems": [],
        "disciplines": [],
        "seed": 1,
        "weak_spot": False,
    },
}
QUESTION = {
    "id": "q1",
    "chapter_id": None,
    "stem_html": "<p>s</p>",
    "options": [{"label": "A", "html": "a"}, {"label": "B", "html": "b"}],
    "difficulty": None,
    "taxonomy_ids": [],
}
SECRET = Explanation(
    answer="B", answer_html="b", explanation_html="<p>e</p>", why_not_html=None, educational_objective="o"
)


@pytest.mark.parametrize(
    ("mode", "attempt", "want"),
    [
        ("tutor", None, (False, False)),
        ("tutor", {"chosen": None, "correct": None, "flagged": True}, (False, False)),  # flag-only
        ("tutor", {"chosen": "A", "correct": None}, (False, False)),  # ungraded
        ("tutor", {"chosen": "A", "correct": False}, (True, True)),
        ("timed", {"chosen": "A", "correct": False}, (True, False)),
    ],
)
def test_tutor_reveal(mode: str, attempt: dict[str, Any] | None, want: tuple[bool, bool]) -> None:
    assert tutor_reveal(mode, attempt) == want


def test_the_shared_serializer_never_emits_a_hidden_field_before_a_graded_answer() -> None:
    view = question_view(BLOCK, 1, QUESTION, None, None, [], graded=False, reveal=False, explanation=SECRET)
    assert view.explanation is None and view.attempt is None
    assert not keys_of(view.model_dump(mode="json")) & set(HIDDEN)
    attempt = {"chosen": "A", "correct": False, "seconds": 3, "flagged": False}
    shown = question_view(BLOCK, 1, QUESTION, attempt, None, [], graded=True, reveal=True, explanation=SECRET)
    assert shown.explanation is not None and shown.attempt is not None and shown.attempt.correct is False
    assert set(HIDDEN) <= keys_of(shown.model_dump(mode="json"))
