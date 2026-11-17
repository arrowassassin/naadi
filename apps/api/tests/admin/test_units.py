"""Pure parts of the admin console: revision diff, coverage aggregation, queue registry rules, YouTube URLs,
raster re-encoding, question form validation."""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from naadi_api.admin import queues
from naadi_api.admin.chapters import diff_bodies, reading_minutes
from naadi_api.admin.coverage import Counts, Item, Node, Taxonomy, aggregate, missing_of, status_of
from naadi_api.admin.media import iso_duration, raster_to_webp, youtube_id_of
from naadi_api.admin.questions import QuestionCreate, item_changed


def p(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_diff_marks_changed_blocks_in_order() -> None:
    old = {"type": "doc", "content": [p("a"), p("b"), {"type": "figure", "attrs": {"mediaId": "f1"}}]}
    new = {"type": "doc", "content": [p("a"), p("B!"), {"type": "figure", "attrs": {"mediaId": "f1"}}, p("c")]}
    ops = [(b.op, b.kind, b.text) for b in diff_bodies(old, new)]
    assert ops == [
        ("equal", "paragraph", "a"),
        ("delete", "paragraph", "b"),
        ("insert", "paragraph", "B!"),
        ("equal", "figure", "f1"),
        ("insert", "paragraph", "c"),
    ]
    assert [b.op for b in diff_bodies(None, new)] == ["insert"] * 4


def test_diff_labels_callouts_and_headings() -> None:
    new = {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "H"}]},
        {"type": "callout", "attrs": {"variant": "trap"}, "content": [p("t")]},
    ]}
    assert [b.kind for b in diff_bodies(None, new)] == ["heading:3", "callout:trap"]


def test_reading_minutes() -> None:
    assert reading_minutes({"type": "doc", "content": [p("word " * 450)]}) == 3
    assert reading_minutes({"type": "doc", "content": [p("x")]}) == 1


def test_coverage_status_and_aggregation() -> None:
    assert status_of(Counts()) == "gap"
    assert status_of(Counts(questions=3)) == "thin"
    assert status_of(Counts(sections=1, videos=1, questions=15, cards=10)) == "covered"
    assert missing_of(Counts(sections=1, videos=0, questions=20, cards=9)) == ["videos", "cards"]
    tax = Taxonomy(
        systems=[Node(id="sys.a", label="A"), Node(id="sys.b", label="B")],
        disciplines=[Node(id="disc.p", label="P")],
        topics=[(Node(id="topic.a1", label="A1"), "sys.a")],
        topic_system={"topic.a1": "sys.a"},
    )
    items = [
        Item("questions", frozenset({"topic.a1", "disc.p"})),  # topic -> system a, cell (a, p)
        Item("questions", frozenset({"sys.b", "disc.p"})),
        Item("sections", frozenset({"topic.a1"})),
        Item("cards", frozenset({"unrelated"})),
    ]
    cells, topics, systems = aggregate(tax, items)
    cell = {(c.system_id, c.discipline_id): c for c in cells}
    assert cell[("sys.a", "disc.p")].counts.questions == 1
    assert cell[("sys.a", "disc.p")].counts.sections == 0  # the section has no discipline tag
    assert cell[("sys.b", "disc.p")].counts.questions == 1
    assert cell[("sys.a", "disc.p")].status == "thin"
    assert topics[0].counts == Counts(sections=1, questions=1)
    assert {s.id: s.counts.questions for s in systems} == {"sys.a": 1, "sys.b": 1}
    assert systems[0].topics == 1 and systems[0].topics_covered == 0


async def _noop_list(ctx: Any, *, limit: int, cursor: str | None) -> queues.QueuePage:
    return queues.QueuePage(items=[], total=0)


async def _noop_decide(*_: Any) -> queues.QueueDecision:
    return queues.QueueDecision(id="x", status="approved")


def test_queue_registry_rules() -> None:
    try:
        queues.register_queue("test_kind", _noop_list, _noop_decide, _noop_decide, ref_type="question")
        with pytest.raises(ValueError, match="already registered"):
            queues.register_queue("test_kind", _noop_list, _noop_decide, _noop_decide, ref_type="question")
        assert "test_kind" in queues.registered_kinds()
    finally:
        queues.unregister_queue("test_kind")
    with pytest.raises(ValueError):
        queues.register_queue("Bad-Kind", _noop_list, _noop_decide, _noop_decide, ref_type="question")
    with pytest.raises(ValueError, match="both"):
        queues.register_queue("half", _noop_list, _noop_decide, None, ref_type="question")
    with pytest.raises(ValueError, match="path"):
        queues.register_queue("pageless", _noop_list, None, None, ref_type="question")
    with pytest.raises(ValueError, match="/admin"):
        queues.register_queue("outside", _noop_list, None, None, ref_type="question", path="/review")
    try:
        queues.register_queue("own_page", _noop_list, None, None, ref_type="question", path="/admin/review", filters=("ai_drafts",))
    finally:
        queues.unregister_queue("own_page")
    # the console's own queues register at import
    assert {"generated_media", "report_issues"} <= set(queues.registered_kinds())


@pytest.mark.parametrize(
    ("url", "vid"),
    [
        ("https://www.youtube.com/watch?v=czY5nyvZ7cU", "czY5nyvZ7cU"),
        ("https://youtu.be/czY5nyvZ7cU?t=30", "czY5nyvZ7cU"),
        ("https://m.youtube.com/watch?v=czY5nyvZ7cU&list=x", "czY5nyvZ7cU"),
        ("https://www.youtube.com/embed/czY5nyvZ7cU", "czY5nyvZ7cU"),
        ("https://www.youtube.com/shorts/czY5nyvZ7cU", "czY5nyvZ7cU"),
        ("https://www.youtube-nocookie.com/embed/czY5nyvZ7cU", "czY5nyvZ7cU"),
        ("https://evil.example/watch?v=czY5nyvZ7cU", None),
        ("https://www.youtube.com.evil.example/watch?v=czY5nyvZ7cU", None),
        ("javascript:alert(1)", None),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/channel/UCabc", None),
    ],
)
def test_youtube_ids(url: str, vid: str | None) -> None:
    assert youtube_id_of(url) == vid


def test_iso_duration() -> None:
    assert iso_duration("PT1H2M3S") == 3723
    assert iso_duration("PT45S") == 45
    assert iso_duration("PT") is None and iso_duration(None) is None and iso_duration("P1D") is None


def test_raster_is_reencoded_to_webp_without_metadata() -> None:
    im = Image.new("RGB", (5000, 100), "white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    data, w, h = raster_to_webp(buf.getvalue())
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert (w, h) == (4096, 82)
    with pytest.raises(ValueError):
        raster_to_webp(b"<svg/>")


def _q(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "exam": "step1",
        "stem_html": "<p>A 30-year-old man …</p>",
        "options": [{"label": c, "html": f"Option {c}"} for c in "ABCDE"],
        "answer": "C",
        "source_id": "naadi-original-v1",
    }
    return {**base, **kw}


def test_question_form_validation() -> None:
    assert QuestionCreate(**_q()).answer == "C"
    for bad in (
        _q(answer="F"),
        _q(options=[{"label": "A", "html": "x"}, {"label": "C", "html": "y"}]),
        _q(options=[{"label": "A", "html": "same"}, {"label": "B", "html": "Same"}]),
        _q(stem_html="<p onclick=alert(1)>x</p>"),
        _q(stem_html="<script>x</script>"),
        _q(explanation_html='<a href="javascript:x">y</a>'),
        _q(taxonomy_ids=["Not Valid"]),
        _q(extra_field=1),
    ):
        with pytest.raises(ValidationError):
            QuestionCreate(**bad)
    # clinical prose such as "Onset = 3 days" is not mistaken for an event handler
    assert QuestionCreate(**_q(stem_html="<p>Onset = 3 days</p>")).stem_html


def test_item_changed_only_for_stem_options_or_key() -> None:
    q = QuestionCreate(**_q())
    before = {"stem_html": q.stem_html, "answer": "C", "options": [{"label": o.label, "html": o.html} for o in q.options]}
    assert not item_changed(before, QuestionCreate(**_q(explanation_html="<p>new</p>")))
    assert not item_changed(before, QuestionCreate(**_q(stem_html="<p>A 30-year-old  man …</p>")))
    assert item_changed(before, QuestionCreate(**_q(answer="B")))
    assert item_changed(before, QuestionCreate(**_q(stem_html="<p>A 31-year-old man</p>")))
