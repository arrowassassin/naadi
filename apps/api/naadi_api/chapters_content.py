"""Server-side scan of a section's TipTap JSON (`chapter_sections.body`).

The node schema is owned by `@naadi/shared/content` (contract 20261116T094551-seg05-seed-6c89be); the build
writes its type lists into `data/generated/section_bodies.json` and `tests/courses/test_content_scan.py`
fails if the constants below drift from it. The scan collects the media and question ids the chapter API
must resolve, and reports node/mark types the reader does not know, so they are logged (with the request id)
when a chapter is served instead of being dropped silently in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTENT_NODE_TYPES = frozenset(
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
        "callout",
        "figure",
        "video",
        "pdf",
        "questionEmbed",
        "mermaid",
        "math",
        "hardBreak",
        "text",
    }
)
CONTENT_MARK_TYPES = frozenset({"bold", "italic", "subscript", "superscript", "highlight"})
MEDIA_NODE_TYPES = frozenset({"figure", "video", "pdf"})

# Bodies are written by the importer and the admin editor, but bound the walk anyway.
MAX_DEPTH = 64
MAX_NODES = 200_000


@dataclass
class ContentScan:
    media_ids: list[str] = field(default_factory=list)
    question_ids: list[str] = field(default_factory=list)
    unknown_nodes: set[str] = field(default_factory=set)
    unknown_marks: set[str] = field(default_factory=set)
    truncated: bool = False


def _add_unique(target: list[str], value: object) -> None:
    if isinstance(value, str) and value and value not in target:
        target.append(value)


def scan_body(body: Any, into: ContentScan | None = None) -> ContentScan:
    out = into or ContentScan()
    if not isinstance(body, dict):
        return out
    stack: list[tuple[Any, int]] = [(body, 0)]
    seen = 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > MAX_NODES or depth > MAX_DEPTH:
            out.truncated = True
            break
        if not isinstance(node, dict):
            out.unknown_nodes.add("<not-an-object>")
            continue
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in CONTENT_NODE_TYPES:
            out.unknown_nodes.add(kind if isinstance(kind, str) else "<missing-type>")
        raw_attrs = node.get("attrs")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        if kind in MEDIA_NODE_TYPES:
            _add_unique(out.media_ids, attrs.get("mediaId"))
        elif kind == "questionEmbed":
            _add_unique(out.question_ids, attrs.get("questionId"))
        for mark in node.get("marks") or []:
            mtype = mark.get("type") if isinstance(mark, dict) else None
            if not isinstance(mtype, str) or mtype not in CONTENT_MARK_TYPES:
                out.unknown_marks.add(mtype if isinstance(mtype, str) else "<missing-type>")
        children = node.get("content")
        if isinstance(children, list):
            stack.extend((c, depth + 1) for c in reversed(children))
    return out
