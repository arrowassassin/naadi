"""TipTap documents the assistant writes: notes, canvas drafts, chapter edit ops.

The model writes plain markdown (headings, lists, paragraphs, **bold**, *italic*, `$math$` stays text); this
module turns it into TipTap JSON on S06's node schema (`chapters_content.CONTENT_NODE_TYPES`), and validates any
TipTap node the model or the student hands back (edit ops, edited previews) against the subset the assistant may
write: no media, no question embeds (the assistant cannot invent media ids or embed a question), bounded size.
"""

from __future__ import annotations

import re
from typing import Any

from ..chapters_content import CONTENT_MARK_TYPES

ASSISTANT_NODE_TYPES = frozenset(
    {
        "doc",
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "table",
        "tableRow",
        "tableHeader",
        "tableCell",
        "mermaid",
        "math",
        "hardBreak",
        "text",
    }
)
# Highlights carry a fixed meaning set by the student (lime / violet / amber): the assistant writes none.
ASSISTANT_MARK_TYPES = frozenset({"bold", "italic", "subscript", "superscript"}) & CONTENT_MARK_TYPES
MAX_NODES = 4000
MAX_TEXT = 40_000
MAX_DEPTH = 24


class InvalidContent(ValueError):
    pass


def _int(v: Any, lo: int, hi: int, default: int) -> int:
    if v is None:
        return default
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise InvalidContent("attr_value")
    return v


def _attrs(kind: str, raw: Any) -> dict[str, Any] | None:
    """The exact attrs S06's strict schema requires for `kind` (packages/shared/src/content/schema.ts)."""
    a = raw if isinstance(raw, dict) else {}
    if raw is not None and not isinstance(raw, dict):
        raise InvalidContent("attrs")
    if kind == "heading":
        return {"level": _int(a.get("level"), 2, 4, 2)}
    if kind == "orderedList":
        t = a.get("type")
        return {"start": _int(a.get("start"), 0, 10_000, 1), "type": t if isinstance(t, str) else None}
    if kind == "table":
        return {"highYield": False}
    if kind in ("tableCell", "tableHeader"):
        return {
            "colspan": _int(a.get("colspan"), 1, 20, 1),
            "rowspan": _int(a.get("rowspan"), 1, 200, 1),
            "colwidth": None,
            "align": None,
        }
    if kind == "mermaid":
        code = a.get("code", a.get("source"))
        if not isinstance(code, str) or not code.strip() or len(code) > 20_000:
            raise InvalidContent("mermaid_code")
        return {"code": code}
    if kind == "math":
        latex = a.get("latex")
        if not isinstance(latex, str) or not latex.strip() or len(latex) > 5000:
            raise InvalidContent("math_latex")
        return {"latex": latex}
    return None


def _check(node: Any, depth: int, counter: list[int]) -> dict[str, Any]:
    counter[0] += 1
    if counter[0] > MAX_NODES or depth > MAX_DEPTH:
        raise InvalidContent("too_large")
    if not isinstance(node, dict):
        raise InvalidContent("not_a_node")
    kind = node.get("type")
    if kind not in ASSISTANT_NODE_TYPES:
        raise InvalidContent(f"node_type:{kind}")
    out: dict[str, Any] = {"type": kind}
    attrs = _attrs(kind, node.get("attrs"))
    if attrs is not None:
        out["attrs"] = attrs
    if kind in ("text", "hardBreak"):
        if kind == "text":
            text = node.get("text")
            if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                raise InvalidContent("text")
            out["text"] = text
        marks = node.get("marks")
        if marks:
            ms = []
            for m in marks:
                if not isinstance(m, dict) or m.get("type") not in ASSISTANT_MARK_TYPES:
                    raise InvalidContent("mark")
                ms.append({"type": m["type"]})
            out["marks"] = ms
        return out
    children = node.get("content")
    if children is not None:
        if not isinstance(children, list):
            raise InvalidContent("content")
        kids = [_check(c, depth + 1, counter) for c in children]
        if kids:
            out["content"] = kids
    if kind in ("bulletList", "orderedList", "listItem", "table", "tableCell", "tableHeader", "doc") and not out.get(
        "content"
    ):
        raise InvalidContent(f"empty:{kind}")
    return out


def validate_node(node: Any) -> dict[str, Any]:
    """A normalised copy of one block-level node (or a doc), or InvalidContent with a structured code."""
    return _check(node, 0, [0])


def validate_doc(doc: Any) -> dict[str, Any]:
    out = validate_node(doc)
    if out["type"] != "doc":
        raise InvalidContent("not_a_doc")
    if not out.get("content"):
        raise InvalidContent("empty")
    return out


# ── markdown → TipTap ──

_INLINE = re.compile(r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_)")


def _inline(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append({"type": "text", "text": part[2:-2], "marks": [{"type": "bold"}]})
        elif (part.startswith("*") and part.endswith("*")) or (part.startswith("_") and part.endswith("_")):
            if len(part) > 2:
                out.append({"type": "text", "text": part[1:-1], "marks": [{"type": "italic"}]})
        else:
            out.append({"type": "text", "text": part})
    return out


def _para(text: str) -> dict[str, Any]:
    content = _inline(text.strip())
    return {"type": "paragraph", "content": content} if content else {"type": "paragraph"}


_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


def markdown_to_doc(md: str) -> dict[str, Any]:
    """A small, predictable subset: headings, bullet/numbered lists, paragraphs, bold/italic, ```mermaid fences."""
    blocks: list[dict[str, Any]] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(_para(" ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            lang = line.strip()[3:].strip().lower()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            src = "\n".join(body).strip()
            if src:
                blocks.append({"type": "mermaid", "attrs": {"code": src}} if lang == "mermaid" else _para(src))
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if m := _HEADING.match(line):
            flush()
            level = max(2, len(m.group(1)))
            blocks.append({"type": "heading", "attrs": {"level": min(level, 4)}, "content": _inline(m.group(2))})
            i += 1
            continue
        for pattern, kind in ((_BULLET, "bulletList"), (_ORDERED, "orderedList")):
            if pattern.match(line):
                flush()
                items = []
                while i < len(lines) and (mm := pattern.match(lines[i])):
                    items.append({"type": "listItem", "content": [_para(mm.group(1))]})
                    i += 1
                blocks.append({"type": kind, "content": items})
                break
        else:
            para.append(line)
            i += 1
    flush()
    if not blocks:
        raise InvalidContent("empty")
    return validate_doc({"type": "doc", "content": blocks})


def doc_text(doc: Any, limit: int = 4000) -> str:
    """Plain text of a TipTap doc (block-separated), for model-facing summaries."""
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == "text":
                parts.append(str(n.get("text", "")))
            elif n.get("type") == "mermaid":
                parts.append(str((n.get("attrs") or {}).get("code", "")))
            for c in n.get("content") or []:
                walk(c)
            if n.get("type") in ("paragraph", "heading", "listItem", "mermaid"):
                parts.append("\n")

    walk(doc)
    text = re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
    return text[:limit]
