"""Strict server-side check of a section body against the content node schema.

The contract is `@naadi/shared/content` (`packages/shared/src/content/schema.ts`, zod, strict objects). The admin
editor validates there too, but the server is the enforcement: a body with an unknown node or mark, a missing or
unexpected attribute, or a bad value is refused with the JSON path of the first problems, so nothing the reader
cannot render is ever published. `tests/admin/test_content_schema.py` runs every seed body through this check and
compares the node/mark/attribute tables with the TypeScript schema source.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
CALLOUT_VARIANTS = ("pearl", "trap", "bridge")
HIGHLIGHT_COLORS = ("lime", "violet", "amber")
HIGHLIGHT_MEANINGS = ("key_fact", "formula", "mistake")
ALIGN = ("left", "center", "right")
MAX_DEPTH = 64
MAX_NODES = 100_000
MAX_PROBLEMS = 20

Path = tuple[str | int, ...]


@dataclass
class Problem:
    path: Path
    code: str  # unknown_node | unknown_mark | bad_attrs | bad_value | bad_content | too_large | not_a_doc


@dataclass
class _Walk:
    problems: list[Problem] = field(default_factory=list)
    nodes: int = 0

    def add(self, path: Path, code: str) -> None:
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(Problem(path, code))


def _int(v: Any, minimum: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and (minimum is None or v >= minimum)


def _nullable(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda v: v is None or check(v)


def _str_len(lo: int, hi: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and lo <= len(v) <= hi


def _enum(values: tuple[Any, ...]) -> Callable[[Any], bool]:
    return lambda v: v in values and not isinstance(v, bool)


def _id(v: Any) -> bool:
    return isinstance(v, str) and bool(ID.match(v))


def _colwidth(v: Any) -> bool:
    return v is None or (isinstance(v, list) and all(_int(x, 0) for x in v))


CELL_ATTRS: dict[str, Callable[[Any], bool]] = {
    "colspan": lambda v: _int(v, 1),
    "rowspan": lambda v: _int(v, 1),
    "colwidth": _colwidth,
    "align": _nullable(_enum(ALIGN)),
}

# node type -> (attribute checks or None for "no attrs key", content kind)
# content kind: "inline" (optional, >= 1 inline), "blocks" (required, >= 1 block), "items", "rows", "cells"
# (required except rows' cells which are optional), None = no content key allowed.
NODE_SPECS: dict[str, tuple[dict[str, Callable[[Any], bool]] | None, str | None]] = {
    "paragraph": (None, "inline"),
    "heading": ({"level": _enum((2, 3, 4))}, "inline"),
    "bulletList": (None, "items"),
    "orderedList": ({"start": _int, "type": _nullable(lambda v: isinstance(v, str))}, "items"),
    "table": ({"highYield": lambda v: isinstance(v, bool)}, "rows"),
    "callout": ({"variant": _enum(CALLOUT_VARIANTS)}, "blocks"),
    "figure": ({"mediaId": _id}, None),
    "video": (
        {"mediaId": _id, "startS": _nullable(lambda v: _int(v, 0)), "endS": _nullable(lambda v: _int(v, 0))},
        None,
    ),
    "pdf": ({"mediaId": _id, "page": _nullable(lambda v: _int(v, 1))}, None),
    "questionEmbed": ({"questionId": _id}, None),
    "mermaid": ({"code": _str_len(1, 20_000)}, None),
    "math": ({"latex": _str_len(1, 5_000)}, None),
}
BLOCK_TYPES = frozenset(NODE_SPECS)
MARK_SPECS: dict[str, dict[str, Callable[[Any], bool]] | None] = {
    "bold": None,
    "italic": None,
    "subscript": None,
    "superscript": None,
    "highlight": {"color": _enum(HIGHLIGHT_COLORS), "meaning": _enum(HIGHLIGHT_MEANINGS)},
}
NODE_TYPES = frozenset({"doc", *NODE_SPECS, "listItem", "tableRow", "tableHeader", "tableCell", "text", "hardBreak"})


def _attrs(w: _Walk, node: dict[str, Any], spec: dict[str, Callable[[Any], bool]] | None, path: Path) -> None:
    if spec is None:
        if "attrs" in node:
            w.add((*path, "attrs"), "bad_attrs")
        return
    attrs = node.get("attrs")
    if not isinstance(attrs, dict) or set(attrs) != set(spec):
        w.add((*path, "attrs"), "bad_attrs")
        return
    for k, check in spec.items():
        if not check(attrs[k]):
            w.add((*path, "attrs", k), "bad_value")


def _keys(w: _Walk, node: dict[str, Any], allowed: set[str], path: Path) -> None:
    extra = set(node) - allowed
    if extra:
        w.add((*path, sorted(extra)[0]), "bad_attrs")


def _marks(w: _Walk, node: dict[str, Any], path: Path) -> None:
    if "marks" not in node:
        return
    marks = node["marks"]
    if not isinstance(marks, list) or not marks:
        w.add((*path, "marks"), "bad_content")
        return
    for i, m in enumerate(marks):
        mp = (*path, "marks", i)
        mtype = m.get("type") if isinstance(m, dict) else None
        if not isinstance(mtype, str) or mtype not in MARK_SPECS:
            w.add(mp, "unknown_mark")
            continue
        _keys(w, m, {"type", "attrs"}, mp)
        _attrs(w, m, MARK_SPECS[mtype], mp)


def _children(w: _Walk, node: dict[str, Any], path: Path, *, required: bool) -> list[Any] | None:
    content = node.get("content")
    if content is None:
        if required:
            w.add((*path, "content"), "bad_content")
        return None
    if not isinstance(content, list) or not content:
        w.add((*path, "content"), "bad_content")
        return None
    return content


def _inline(w: _Walk, node: Any, path: Path) -> None:
    w.nodes += 1
    t = node.get("type") if isinstance(node, dict) else None
    if t == "text":
        _keys(w, node, {"type", "text", "marks"}, path)
        if not isinstance(node.get("text"), str) or not node["text"]:
            w.add((*path, "text"), "bad_value")
        _marks(w, node, path)
    elif t == "hardBreak":
        _keys(w, node, {"type", "marks"}, path)
        _marks(w, node, path)
    else:
        w.add(path, "unknown_node" if t not in NODE_TYPES else "bad_content")


def _block(w: _Walk, node: Any, path: Path, depth: int) -> None:
    w.nodes += 1
    if depth > MAX_DEPTH or w.nodes > MAX_NODES:
        w.add(path, "too_large")
        return
    t = node.get("type") if isinstance(node, dict) else None
    if not isinstance(t, str) or t not in BLOCK_TYPES:
        w.add(path, "unknown_node" if t not in NODE_TYPES else "bad_content")
        return
    assert isinstance(node, dict)
    attr_spec, content_kind = NODE_SPECS[t]
    _keys(w, node, {"type", "attrs", "content"} if content_kind else {"type", "attrs"}, path)
    _attrs(w, node, attr_spec, path)
    if content_kind is None:
        return
    kids = _children(w, node, path, required=content_kind != "inline")
    for i, child in enumerate(kids or []):
        cp = (*path, "content", i)
        if content_kind == "inline":
            _inline(w, child, cp)
        elif content_kind == "blocks":
            _block(w, child, cp, depth + 1)
        elif content_kind == "items":
            _wrapper(w, child, cp, depth + 1, ("listItem",))
        elif content_kind == "rows":
            _row(w, child, cp, depth + 1)


def _wrapper(w: _Walk, node: Any, path: Path, depth: int, types: tuple[str, ...]) -> None:
    """listItem / tableCell / tableHeader: blocks inside (cells also carry attrs)."""
    w.nodes += 1
    t = node.get("type") if isinstance(node, dict) else None
    if t not in types:
        w.add(path, "unknown_node" if t not in NODE_TYPES else "bad_content")
        return
    assert isinstance(node, dict)
    if t == "listItem":
        _keys(w, node, {"type", "content"}, path)
    else:
        _keys(w, node, {"type", "attrs", "content"}, path)
        _attrs(w, node, CELL_ATTRS, path)
    for i, child in enumerate(_children(w, node, path, required=True) or []):
        _block(w, child, (*path, "content", i), depth + 1)


def _row(w: _Walk, node: Any, path: Path, depth: int) -> None:
    w.nodes += 1
    t = node.get("type") if isinstance(node, dict) else None
    if t != "tableRow":
        w.add(path, "unknown_node" if t not in NODE_TYPES else "bad_content")
        return
    assert isinstance(node, dict)
    _keys(w, node, {"type", "content"}, path)
    for i, cell in enumerate(_children(w, node, path, required=False) or []):
        _wrapper(w, cell, (*path, "content", i), depth + 1, ("tableCell", "tableHeader"))


def check_doc(body: Any) -> list[Problem]:
    """Every problem (up to MAX_PROBLEMS) of `body` as a stored document; [] when it is valid."""
    w = _Walk()
    if not isinstance(body, dict) or body.get("type") != "doc":
        return [Problem((), "not_a_doc")]
    _keys(w, body, {"type", "content"}, ())
    for i, child in enumerate(_children(w, body, (), required=True) or []):
        _block(w, child, ("content", i), 1)
    return w.problems


def refs_of(body: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """([(node type, media id)], [question id]) in document order, unique, for a valid document."""
    media: list[tuple[str, str]] = []
    questions: list[str] = []
    stack = [body]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        raw_attrs = n.get("attrs")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        if t in ("figure", "video", "pdf") and isinstance(attrs.get("mediaId"), str):
            pair = (str(t), attrs["mediaId"])
            if pair not in media:
                media.append(pair)
        elif t == "questionEmbed" and isinstance(attrs.get("questionId"), str) and attrs["questionId"] not in questions:
            questions.append(attrs["questionId"])
        kids = n.get("content")
        if isinstance(kids, list):
            stack.extend(reversed(kids))
    return media, questions


def text_of(node: Any) -> str:
    """Plain text of a document (as `textOf` in schema.ts): blocks separated by spaces."""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if not isinstance(n, dict):
            return
        if n.get("type") == "text" and isinstance(n.get("text"), str):
            parts.append(n["text"])
        elif n.get("type") == "hardBreak":
            parts.append(" ")
        kids = n.get("content")
        if isinstance(kids, list):
            for c in kids:
                walk(c)
            parts.append(" ")

    walk(node)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def word_count(body: Any) -> int:
    text = text_of(body)
    return len(text.split()) if text else 0
