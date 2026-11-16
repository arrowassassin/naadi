"""Card faces: the student's plain text, the TipTap document ContentRenderer draws, and the interchange HTML.

A face is written as plain text: a blank line starts a new paragraph, a single newline is a line break. Nothing
the student types is interpreted as markup, so a card can never inject HTML or unknown nodes. Faces stored
only as HTML (other origins, before front_doc existed) are shown as their text.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

MAX_FACE_CHARS = 4000
_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote"}


def normalize_text(text: str) -> str:
    """Trim, unify newlines, collapse 3+ newlines to a paragraph break, strip trailing spaces per line."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def text_to_doc(text: str) -> dict[str, Any]:
    paragraphs = [p for p in normalize_text(text).split("\n\n") if p]
    content: list[dict[str, Any]] = []
    for p in paragraphs:
        inline: list[dict[str, Any]] = []
        for i, line in enumerate(p.split("\n")):
            if i:
                inline.append({"type": "hardBreak"})
            if line:
                inline.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": inline})
    return {"type": "doc", "content": content}


def doc_to_text(doc: object) -> str:
    """The plain text of a doc written by `text_to_doc` (any other doc: its text, paragraphs separated)."""
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        return ""
    paragraphs: list[str] = []

    def inline(node: object) -> str:
        if not isinstance(node, dict):
            return ""
        if node.get("type") == "text":
            return str(node.get("text", ""))
        if node.get("type") == "hardBreak":
            return "\n"
        return "".join(inline(c) for c in node.get("content", []) or [])

    for block in doc.get("content", []) or []:
        paragraphs.append(inline(block))
    return normalize_text("\n\n".join(paragraphs))


def text_to_html(text: str) -> str:
    paragraphs = [p for p in normalize_text(text).split("\n\n") if p]
    return "".join("<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>" for p in paragraphs)


class _TextOf(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS and tag != "br":
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    p = _TextOf()
    p.feed(markup)
    p.close()
    return normalize_text(re.sub(r"[ \t]+", " ", "".join(p.parts)))


def face_doc(doc: object, markup: str) -> dict[str, Any]:
    """What ContentRenderer gets for a face: the stored doc, else the stored HTML's text as a doc."""
    if isinstance(doc, dict) and doc.get("type") == "doc":
        return doc
    return text_to_doc(html_to_text(markup))


def face_text(doc: object, markup: str) -> str:
    return doc_to_text(doc) if isinstance(doc, dict) else html_to_text(markup)
