"""Unit: `notes.body_text` extraction (TipTap JSON and Excalidraw scenes), excerpts and the shape rules."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from naadi_api.notes.models import AnchorPos, NoteCreate, NotePatch, PageCreate, check_shape
from naadi_api.notes.text import MAX_BODY_TEXT, body_text, excalidraw_text, excerpt, json_depth, valid_tiptap


def t(text: str, *marks: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": m, "attrs": {"color": "lime"}} if m == "highlight" else {"type": m} for m in marks]
    return node


def doc(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(content)}


def p(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(content)}


def test_tiptap_blocks_marks_lists_and_breaks() -> None:
    body = doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [t("Renal tubule")]},
        p(t("The "), t("loop", "bold"), t(" of "), t("Henle", "highlight"), t(" concentrates.")),
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [p(t("PCT: "), t("isotonic", "italic"))]},
                {
                    "type": "listItem",
                    "content": [
                        p(t("TAL")),
                        {"type": "orderedList", "content": [{"type": "listItem", "content": [p(t("NKCC2"))]}]},
                    ],
                },
            ],
        },
        p(t("line one"), {"type": "hardBreak"}, t("line two")),
        {"type": "image", "attrs": {"src": "user-uploads/u/x.png", "alt": "diagram"}},
        p(),
    )
    assert body_text(body).split("\n") == [
        "Renal tubule",
        "The loop of Henle concentrates.",
        "PCT: isotonic",
        "TAL",
        "NKCC2",
        "line one line two",
    ]


@pytest.mark.parametrize("bad", [None, "text", [], {"type": "doc"}, {"content": 3}, {"type": "doc", "content": [1]}])
def test_unreadable_bodies_have_no_text(bad: Any) -> None:
    assert body_text(bad) == ""


def test_tiptap_shape_check() -> None:
    assert valid_tiptap(doc(p(t("a"))))
    for bad in (
        {"type": "doc", "content": 3},
        {"type": "doc", "content": [{"text": "no type"}]},
        doc(p({"type": "text", "text": 5})),
        doc(p({"type": "text", "text": "x", "marks": "bold"})),
        {"type": "paragraph"},
    ):
        assert not valid_tiptap(bad)


def test_deep_or_huge_bodies_are_not_walked() -> None:
    deep: dict[str, Any] = p(t("bottom"))
    for _ in range(80):
        deep = {"type": "blockquote", "content": [deep]}
    assert json_depth(deep) > 64
    assert body_text(doc(deep)) == ""
    long = doc(*[p(t("x" * 1000)) for _ in range(150)])
    assert len(body_text(long)) == MAX_BODY_TEXT


def test_whiteboard_text_elements_in_reading_order() -> None:
    scene = {
        "type": "excalidraw",
        "elements": [
            {"type": "text", "x": 400, "y": 10, "text": "right", "originalText": "right"},
            {"type": "freedraw", "x": 0, "y": 0, "points": [[0, 0], [5, 5]]},
            {"type": "text", "x": 10, "y": 12, "text": "wrapped\nleft", "originalText": "left  side"},
            {"type": "text", "x": 10, "y": 300, "text": "gone", "isDeleted": True},
            {"type": "rectangle", "x": 1, "y": 1},
            {"type": "text", "x": 10, "y": 200, "text": "below"},
            {"type": "text", "x": "bad", "y": None, "text": "  "},
        ],
        "appState": {"viewBackgroundColor": "#fff"},
    }
    assert excalidraw_text(scene) == "left side\nright\nbelow"
    assert body_text(scene, "whiteboard") == "left side\nright\nbelow"
    assert body_text(scene, "paper") == ""  # a scene is not TipTap
    assert body_text(doc(p(t("x"))), "whiteboard") == ""


def test_excerpt_is_one_line_and_cut_at_a_word() -> None:
    assert excerpt(None) == "" and excerpt("a\n\nb") == "a b"
    text = " ".join(["word"] * 100)
    out = excerpt(text, 50)
    assert out.endswith("…") and len(out) <= 51 and "  " not in out
    assert excerpt("x" * 400, 10) == "x" * 10 + "…"


# ── shape rules (mirrored by the 0017 check constraints) ──


def pos(**over: Any) -> dict[str, Any]:
    return {"from": 3, "to": 9, "quote": "Henle", "prefix": "loop of ", "suffix": " concentrates", **over}


def test_anchor_pos_round_trips_with_the_from_alias() -> None:
    a = AnchorPos.model_validate(pos(revision="r1"))
    assert a.from_ == 3
    assert a.model_dump(by_alias=True, exclude_none=True) == pos(revision="r1")
    for bad in (pos(to=2), pos(quote=""), pos(prefix="x" * 65), pos(extra=1), pos(**{"from": -1})):
        with pytest.raises(ValidationError):
            AnchorPos.model_validate(bad)


def test_highlights_need_an_anchor_a_position_and_a_colour() -> None:
    ok = {
        "kind": "highlight",
        "anchor_type": "section",
        "anchor_id": "s1",
        "anchor_pos": pos(),
        "highlight_color": "lime",
    }
    assert NoteCreate.model_validate(ok).kind == "highlight"
    for over in (
        {"highlight_color": None},
        {"anchor_pos": None},
        {"anchor_type": "video"},
        {"anchor_type": "free", "anchor_id": None},
        {"notebook_id": str(uuid.uuid4())},
        {"canvas": "whiteboard"},
        {"highlight_color": "red"},
    ):
        with pytest.raises(ValidationError):
            NoteCreate.model_validate({**ok, **over})


def test_only_highlights_carry_a_colour_and_anchors_name_their_target() -> None:
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"kind": "note", "highlight_color": "lime"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"anchor_type": "section"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"anchor_type": "free", "anchor_id": "x"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"kind": "page"})  # pages come from POST /api/notebooks/{id}/pages
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"anchor_type": "section", "anchor_id": "bad id with spaces"})
    assert NoteCreate.model_validate({}).anchor_type is None
    with pytest.raises(ValueError, match="free note"):
        check_shape(
            kind="page", anchor_type=None, anchor_id="x", anchor_pos=None, color=None, notebook_id=None, canvas="paper"
        )


def test_patch_and_page_pairs() -> None:
    with pytest.raises(ValidationError):
        NotePatch.model_validate({"anchor_type": "section"})
    with pytest.raises(ValidationError):
        NotePatch.model_validate({"canvas": None})
    assert NotePatch.model_validate({"title": None}).model_fields_set == {"title"}
    with pytest.raises(ValidationError):
        PageCreate.model_validate({"anchor_id": "s1"})
    with pytest.raises(ValidationError):
        PageCreate.model_validate({"canvas": "slides"})


def test_list_bodies_stay_within_the_response_budget() -> None:
    from naadi_api.notes.routes import within_budget

    rows = [{"body": doc(p(t("x" * 100))), "body_omitted": False} for _ in range(5)]
    out = within_budget(rows, budget=400)
    assert [r["body"] is not None for r in out] == [True, True, False, False, False]
    assert all(r["body_omitted"] for r in out[2:]) and not rows[2]["body_omitted"]
