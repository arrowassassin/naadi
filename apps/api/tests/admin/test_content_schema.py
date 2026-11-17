"""The server's strict body check (naadi_api.admin.content_schema) against the zod contract and the seed bodies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from naadi_api.admin import content_schema as cs
from naadi_api.chapters_content import CONTENT_MARK_TYPES, CONTENT_NODE_TYPES

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_TS = (ROOT / "packages/shared/src/content/schema.ts").read_text(encoding="utf-8")
EXT_TS = (ROOT / "packages/shared/src/content/extensions.ts").read_text(encoding="utf-8")


def _ts_tuple(name: str, src: str) -> tuple[str, ...]:
    m = re.search(rf"export const {name} = \[(.*?)\] as const", src, re.S)
    assert m, name
    return tuple(re.findall(r'"([^"]+)"', m.group(1)))


def test_node_and_mark_tables_match_the_typescript_contract() -> None:
    assert cs.NODE_TYPES == frozenset(_ts_tuple("CONTENT_NODE_TYPES", SCHEMA_TS)) == CONTENT_NODE_TYPES
    assert frozenset(cs.MARK_SPECS) == frozenset(_ts_tuple("CONTENT_MARK_TYPES", SCHEMA_TS)) == CONTENT_MARK_TYPES
    assert cs.CALLOUT_VARIANTS == _ts_tuple("CALLOUT_VARIANTS", EXT_TS)
    assert cs.HIGHLIGHT_COLORS == _ts_tuple("HIGHLIGHT_COLORS", EXT_TS)
    assert cs.HIGHLIGHT_MEANINGS == _ts_tuple("HIGHLIGHT_MEANINGS", EXT_TS)


def test_block_attribute_names_match_the_zod_strict_objects() -> None:
    for node, (attrs, _content) in cs.NODE_SPECS.items():
        m = re.search(rf'type: z\.literal\("{node}"\),\s*attrs: z\.strictObject\(\{{(.*?)\}}\)', SCHEMA_TS, re.S)
        if attrs is None:
            assert m is None, node
            continue
        assert m, node
        assert set(re.findall(r"(\w+):", m.group(1))) == set(attrs), node


def test_every_seed_body_passes() -> None:
    data = json.loads((ROOT / "data/generated/section_bodies.json").read_text(encoding="utf-8"))
    bodies = [v["body"] for v in data["sections"].values() if v.get("body")]
    assert len(bodies) > 100
    for b in bodies:
        assert cs.check_doc(b) == []


def doc(*blocks: Any) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


def para(text: str = "x", **extra: Any) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text, **extra}]}


def codes(body: Any) -> list[tuple[tuple[str | int, ...], str]]:
    return [(p.path, p.code) for p in cs.check_doc(body)]


def test_valid_rich_document() -> None:
    cell = {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None}, "content": [para()]}
    body = doc(
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "H"}]},
        para("bold", marks=[{"type": "bold"}, {"type": "highlight", "attrs": {"color": "lime", "meaning": "key_fact"}}]),
        {"type": "bulletList", "content": [{"type": "listItem", "content": [para()]}]},
        {"type": "orderedList", "attrs": {"start": 1, "type": None}, "content": [{"type": "listItem", "content": [para()]}]},
        {"type": "table", "attrs": {"highYield": True}, "content": [{"type": "tableRow", "content": [cell]}]},
        {"type": "callout", "attrs": {"variant": "pearl"}, "content": [para()]},
        {"type": "figure", "attrs": {"mediaId": "fig-1"}},
        {"type": "video", "attrs": {"mediaId": "yt-a", "startS": 0, "endS": 30}},
        {"type": "pdf", "attrs": {"mediaId": "pdf-1", "page": 2}},
        {"type": "questionEmbed", "attrs": {"questionId": "ch01-q01"}},
        {"type": "mermaid", "attrs": {"code": "graph TD; A-->B"}},
        {"type": "math", "attrs": {"latex": "x^2"}},
        {"type": "paragraph"},
    )
    assert codes(body) == []
    media, questions = cs.refs_of(body)
    assert media == [("figure", "fig-1"), ("video", "yt-a"), ("pdf", "pdf-1")]
    assert questions == ["ch01-q01"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"type": "paragraph"}, [((), "not_a_doc")]),
        (doc(), [(("content",), "bad_content")]),
        (doc({"type": "iframe", "attrs": {"src": "x"}}), [(("content", 0), "unknown_node")]),
        (doc(para(marks=[{"type": "link", "attrs": {"href": "x"}}])), [(("content", 0, "content", 0, "marks", 0), "unknown_mark")]),
        (doc({"type": "figure", "attrs": {"mediaId": "Bad Id"}}), [(("content", 0, "attrs", "mediaId"), "bad_value")]),
        (doc({"type": "figure", "attrs": {"mediaId": "a", "src": "x"}}), [(("content", 0, "attrs"), "bad_attrs")]),
        (doc({"type": "heading", "attrs": {"level": 1}}), [(("content", 0, "attrs", "level"), "bad_value")]),
        (doc({"type": "heading", "attrs": {"level": True}}), [(("content", 0, "attrs", "level"), "bad_value")]),
        (doc({"type": "callout", "attrs": {"variant": "note"}, "content": [para()]}), [(("content", 0, "attrs", "variant"), "bad_value")]),
        (doc({"type": "paragraph", "content": [{"type": "text", "text": ""}]}), [(("content", 0, "content", 0, "text"), "bad_value")]),
        (doc({"type": "paragraph", "style": "x"}), [(("content", 0, "style"), "bad_attrs")]),
        (doc({"type": "bulletList", "content": [para()]}), [(("content", 0, "content", 0), "bad_content")]),
        (doc({"type": "text", "text": "loose"}), [(("content", 0), "bad_content")]),
        (doc({"type": "math", "attrs": {"latex": ""}}), [(("content", 0, "attrs", "latex"), "bad_value")]),
    ],
)
def test_refusals_name_the_path(body: Any, expected: list[Any]) -> None:
    assert codes(body) == expected


def test_depth_is_bounded() -> None:
    node: dict[str, Any] = para()
    for _ in range(80):
        node = {"type": "callout", "attrs": {"variant": "trap"}, "content": [node]}
    assert ("too_large" in {c for _p, c in codes(doc(node))})


def test_text_and_word_count() -> None:
    body = doc(para("one two"), {"type": "paragraph", "content": [{"type": "text", "text": "three"}, {"type": "hardBreak"}]})
    assert cs.text_of(body) == "one two three"
    assert cs.word_count(body) == 3
    assert cs.word_count(None) == 0
