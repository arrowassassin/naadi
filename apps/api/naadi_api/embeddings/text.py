"""Plain text out of the formats Naadi stores: TipTap JSON, HTML fragments and Markdown.

The retrieval index holds plain text only. Nothing here knows about answers: callers decide which fields
they pass in (a question's stem and options, never its answer or explanation).
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_EMPH = re.compile(r"(\*\*|__|\*|_|`)")
_MD_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_MD_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.M)
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)*\s*\|?\s*$", re.M)

BLOCK_TYPES = frozenset({"paragraph", "heading", "listItem", "tableRow", "blockquote", "codeBlock"})


def clean(s: str) -> str:
    """Collapse runs of spaces, keep paragraph breaks."""
    lines = [_WS.sub(" ", line).strip() for line in s.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    spaced = re.sub(r"</(p|div|li|tr|h[1-6]|blockquote)>|<br\s*/?>", "\n", fragment, flags=re.I)
    spaced = re.sub(r"</t[dh]>", " ", spaced, flags=re.I)
    # Inline tags (b, i, sup, a, ...) vanish without leaving a gap: "<b>X</b>." stays "X."
    return clean(html.unescape(_TAGS.sub("", spaced)))


def markdown_to_text(md: str | None) -> str:
    if not md:
        return ""
    s = _MD_TABLE_SEP.sub("", md)
    s = _MD_LINK.sub(lambda m: m.group(1), s)
    s = _MD_HEADING.sub("", s)
    s = _MD_RULE.sub("", s)
    s = _MD_BULLET.sub("", s)
    s = _MD_EMPH.sub("", s)
    s = s.replace("|", " ; ")
    return clean(html.unescape(_TAGS.sub(" ", s)))


def _inline(node: Mapping[str, Any]) -> str:
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return " "
    return "".join(_inline(c) for c in node.get("content") or [] if isinstance(c, Mapping))


def tiptap_to_text(doc: Mapping[str, Any] | None, figure_captions: Mapping[str, str] | None = None) -> str:
    """One line per block (paragraph, heading, list item, table row); figures contribute their caption.

    `figure_captions` maps a media id to the caption of an approved asset whose source allows AI ingest;
    a figure that is not in the map contributes nothing.
    """
    if not isinstance(doc, Mapping):
        return ""
    lines: list[str] = []

    def walk(node: Mapping[str, Any]) -> None:
        kind = node.get("type")
        if kind == "figure":
            attrs = node.get("attrs") or {}
            caption = (figure_captions or {}).get(str(attrs.get("mediaId") or ""))
            if caption:
                lines.append(f"Figure: {caption}")
            return
        if kind == "tableRow":
            cells = [_inline(c).strip() for c in node.get("content") or [] if isinstance(c, Mapping)]
            row = " ; ".join(c for c in cells if c)
            if row:
                lines.append(row)
            return
        children = [c for c in node.get("content") or [] if isinstance(c, Mapping)]
        if kind in BLOCK_TYPES and not any(c.get("type") in BLOCK_TYPES or c.get("type") == "figure" for c in children):
            text = _inline(node).strip()
            if text:
                lines.append(text)
            return
        for c in children:
            walk(c)

    walk(doc)
    return clean("\n".join(lines))
