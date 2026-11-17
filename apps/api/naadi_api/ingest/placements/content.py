"""Builders shared by the built-in placements: TipTap documents (S06 node schema) and HTML from plain text, ids.

Everything here escapes: text from a student's file becomes text nodes or escaped HTML, never markup.
"""

from __future__ import annotations

import html
import re
import uuid
from typing import Any

MAX_BLOCKS = 4000
_BULLET = re.compile(r"^\s*(?:[-*•▪◦●]|\d{1,3}[.)])\s+(.+)$")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_ID_OK = re.compile(r"[^a-z0-9_-]")


def short_id(item_id: uuid.UUID) -> str:
    return item_id.hex[:20]


def chapter_id(item_id: uuid.UUID) -> str:
    return f"u-{short_id(item_id)}"


def section_id(item_id: uuid.UUID, n: int) -> str:
    return f"u-{short_id(item_id)}-s{n}"


def media_id(item_id: uuid.UUID, key: str) -> str:
    return f"u-{short_id(item_id)}-{_ID_OK.sub('', key.lower())[:40]}"


def question_id(item_id: uuid.UUID, n: int) -> str:
    return f"u-{short_id(item_id)}-q{n}"


def _text(s: str) -> list[dict[str, Any]] | None:
    s = s.strip()
    return [{"type": "text", "text": s}] if s else None


def _paragraph(s: str) -> dict[str, Any]:
    content = _text(s)
    return {"type": "paragraph", "content": content} if content else {"type": "paragraph"}


def _table(rows: list[list[str]]) -> dict[str, Any] | None:
    rows = [r for r in rows if any(c.strip() for c in r)][:200]
    if len(rows) < 2:
        return None
    width = max(len(r) for r in rows)
    attrs = {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None}
    out_rows = []
    for i, r in enumerate(rows):
        cells = []
        for j in range(width):
            v = r[j] if j < len(r) else ""
            cells.append(
                {
                    "type": "tableHeader" if i == 0 else "tableCell",
                    "attrs": dict(attrs),
                    "content": [_paragraph(v[:2000])],
                }
            )
        out_rows.append({"type": "tableRow", "content": cells})
    return {"type": "table", "attrs": {"highYield": False}, "content": out_rows}


def _pipe_row(line: str) -> list[str] | None:
    s = line.strip()
    if s.startswith("|") and s.endswith("|") and len(s) > 2:
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            return []
        return cells
    return None


def blocks_from_text(text: str) -> list[dict[str, Any]]:
    """Paragraphs, bullet lists, level-3/4 headings and Markdown pipe tables (as TipTap block nodes)."""
    blocks: list[dict[str, Any]] = []
    bullets: list[str] = []
    table: list[list[str]] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(_paragraph(" ".join(para)))
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            blocks.append(
                {"type": "bulletList", "content": [{"type": "listItem", "content": [_paragraph(b)]} for b in bullets]}
            )
            bullets.clear()

    def flush_table() -> None:
        if table:
            node = _table(table)
            if node:
                blocks.append(node)
            else:
                para.extend(" | ".join(r) for r in table)
            table.clear()

    for line in text.splitlines():
        if len(blocks) >= MAX_BLOCKS:
            break
        row = _pipe_row(line)
        if row is not None:
            flush_para()
            flush_bullets()
            if row:
                table.append(row)
            continue
        flush_table()
        if not line.strip():
            flush_para()
            flush_bullets()
            continue
        mh = _MD_HEADING.match(line)
        if mh:
            flush_para()
            flush_bullets()
            content = _text(mh.group(2)[:300])
            if content:
                blocks.append(
                    {"type": "heading", "attrs": {"level": 3 if len(mh.group(1)) <= 3 else 4}, "content": content}
                )
            continue
        mb = _BULLET.match(line)
        if mb:
            flush_para()
            bullets.append(mb.group(1)[:4000])
            continue
        flush_bullets()
        para.append(line.strip())
    flush_table()
    flush_para()
    flush_bullets()
    return blocks


def doc(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "doc", "content": blocks or [{"type": "paragraph"}]}


def html_from_text(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)[:200_000]


def reading_minutes(text: str) -> int:
    return max(1, min(600, round(len(text.split()) / 200)))


__all__ = [
    "blocks_from_text",
    "chapter_id",
    "doc",
    "html_from_text",
    "media_id",
    "question_id",
    "reading_minutes",
    "section_id",
    "short_id",
]
