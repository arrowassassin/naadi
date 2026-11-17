"""`notes.body_text`: the plain text of a note, for search and the notes list. Pure functions only.

- `canvas 'paper'`: `body` is TipTap JSON (StarterKit nodes plus highlight marks and images). Text comes from
  `embeddings.text.tiptap_to_text`, the same walk the chapter index uses (one line per block, marks dropped,
  images contribute nothing), so a note reads the same in the index and in the list.
- `canvas 'whiteboard'`: `body` is an Excalidraw scene (`{elements: [...], appState?, files?}`). The text elements
  that are not deleted are read top to bottom, then left to right; freehand, shapes and images contribute nothing
  (Excalidraw freehand is not OCR'd, segment 13 decision).

Both walks are bounded: at most `MAX_NODES` nodes are visited and the result is cut at `MAX_BODY_TEXT` characters,
so a hostile body costs a bounded amount of work. Nesting depth is checked separately by `json_depth`, before any
walk, so the recursive TipTap walk cannot run out of stack.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ..embeddings.text import clean, tiptap_to_text

Canvas = Literal["paper", "whiteboard"]

MAX_BODY_TEXT = 100_000
MAX_NODES = 200_000
MAX_DEPTH = 64


def json_depth(value: Any, limit: int = MAX_DEPTH) -> int:
    """Nesting depth of a JSON value (iterative); stops counting at `limit + 1`."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, Mapping):
            children: Sequence[Any] = list(v.values())
        elif isinstance(v, list):
            children = v
        else:
            continue
        deepest = max(deepest, d)
        if deepest > limit:
            return deepest
        stack.extend((c, d + 1) for c in children)
    return deepest


def json_nodes(value: Any, limit: int = MAX_NODES) -> int:
    """Number of objects and arrays in a JSON value (iterative); stops counting at `limit + 1`."""
    n = 0
    stack: list[Any] = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, Mapping):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        else:
            continue
        n += 1
        if n > limit:
            return n
    return n


def valid_tiptap(body: Any) -> bool:
    """A TipTap document's node shape: `type` a string, `content` a list of nodes, `text` a string, `marks` a list
    (iterative, bounded by MAX_NODES). Attributes are not inspected."""
    if not isinstance(body, Mapping) or body.get("type") != "doc":
        return False
    stack: list[Any] = [body]
    seen = 0
    while stack:
        node = stack.pop()
        seen += 1
        if seen > MAX_NODES or not isinstance(node, Mapping) or not isinstance(node.get("type"), str):
            return False
        if "text" in node and not isinstance(node["text"], str):
            return False
        if "marks" in node and not isinstance(node["marks"], list):
            return False
        content = node.get("content")
        if content is not None:
            if not isinstance(content, list):
                return False
            stack.extend(content)
    return True


def _num(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) else 0.0


def excalidraw_text(scene: Mapping[str, Any] | None) -> str:
    """Text elements of an Excalidraw scene, in reading order (rows of 24 scene px, then x)."""
    if not isinstance(scene, Mapping):
        return ""
    elements = scene.get("elements")
    if not isinstance(elements, list):
        return ""
    found: list[tuple[float, float, str]] = []
    for el in elements[:MAX_NODES]:
        if not isinstance(el, Mapping) or el.get("type") != "text" or el.get("isDeleted") is True:
            continue
        raw = el.get("originalText")
        if not isinstance(raw, str) or not raw.strip():
            raw = el.get("text")
        if isinstance(raw, str) and raw.strip():
            found.append((_num(el.get("y")), _num(el.get("x")), raw))
    found.sort(key=lambda t: (math.floor(t[0] / 24.0), t[1]))
    return clean("\n".join(t for _, _, t in found))


def body_text(body: Mapping[str, Any] | None, canvas: Canvas = "paper") -> str:
    """The plain text of a note body (see the module docstring); "" for an empty or unreadable body."""
    if not isinstance(body, Mapping):
        return ""
    if json_depth(body) > MAX_DEPTH or json_nodes(body) > MAX_NODES:
        return ""
    if canvas == "whiteboard":
        return excalidraw_text(body)[:MAX_BODY_TEXT]
    if not valid_tiptap(body):
        return ""
    return tiptap_to_text(body)[:MAX_BODY_TEXT]


def excerpt(text: str | None, limit: int = 280) -> str:
    """The first `limit` characters of `text` on one line, cut at a word boundary when one is near."""
    if not text:
        return ""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    cut = one[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


__all__ = [
    "MAX_BODY_TEXT",
    "MAX_DEPTH",
    "MAX_NODES",
    "body_text",
    "excalidraw_text",
    "excerpt",
    "json_depth",
    "valid_tiptap",
]
