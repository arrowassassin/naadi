"""Card HTML: cleaned to the notes allowlist, converted to the TipTap documents ContentRenderer draws, and back.

Anki fields are arbitrary HTML written by deck authors (the content of a downloaded deck is untrusted). Nothing of
it is kept as markup: it is parsed with the stdlib parser into the strict node set of `packages/shared` content
(`paragraph`, `heading`, lists, `table`, marks bold / italic / subscript / superscript, `hardBreak`, `figure`), and
everything else (scripts, styles, iframes, event handlers, classes, links' targets) is dropped. Text survives.
`front_html` / `back_html` are regenerated from that document, so stored HTML is always the cleaned form.
"""

from __future__ import annotations

import html as htmllib
import re
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

Node = dict[str, Any]

MAX_DEPTH = 24
MAX_NODES = 20_000
BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "pre", "center", "figure", "figcaption"}
HEADINGS = {"h1": 2, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}
BOLD = {"b", "strong"}
ITALIC = {"i", "em", "cite", "dfn"}
SKIP = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "head", "title"}
AUDIO = {"audio", "video", "source", "track"}
SOUND = re.compile(r"\[sound:[^\]]*\]", re.IGNORECASE)
_WS = re.compile(r"[ \t\r\n\f ]+")
_MARK = {
    "bold": {"type": "bold"},
    "italic": {"type": "italic"},
    "sub": {"type": "subscript"},
    "sup": {"type": "superscript"},
}

# What the converter reports back (counts only; the API turns them into a report).
ImageResolver = Callable[[str], str | None]


class Stats:
    def __init__(self) -> None:
        self.audio = 0
        self.images_missing = 0
        self.images: list[str] = []


class _Builder(HTMLParser):
    def __init__(self, resolve: ImageResolver | None, stats: Stats) -> None:
        super().__init__(convert_charrefs=True)
        self.resolve = resolve
        self.stats = stats
        self.blocks: list[Node] = []
        # Stack of open containers; each is (kind, children list, attrs). The root collects blocks.
        self.stack: list[tuple[str, list[Node], dict[str, Any]]] = [("root", self.blocks, {})]
        self.inline: list[Node] = []  # inline nodes of the current textblock
        self.marks: list[str] = []
        self.skip = 0
        self.audio = 0
        self.nodes = 0
        self.heading: int | None = None
        self.cell: tuple[str, dict[str, Any]] | None = None

    # ── helpers ──
    def _children(self) -> list[Node]:
        return self.stack[-1][1]

    def _flush(self, kind: str = "paragraph", attrs: dict[str, Any] | None = None) -> None:
        nodes = self.inline
        self.inline = []
        # Trim leading/trailing hard breaks and whitespace-only text.
        while nodes and (nodes[0]["type"] == "hardBreak" or not nodes[0].get("text", "x").strip()):
            nodes.pop(0)
        while nodes and (nodes[-1]["type"] == "hardBreak" or not nodes[-1].get("text", "x").strip()):
            nodes.pop()
        if not nodes:
            return
        nodes[0] = _ltrim(nodes[0])
        nodes[-1] = _rtrim(nodes[-1])
        node: Node = {"type": kind, "content": nodes}
        if attrs:
            node["attrs"] = attrs
        self._add(node)

    def _add(self, node: Node) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            return
        parent_kind = self.stack[-1][0]
        if parent_kind == "list" and node["type"] != "listItem":
            # Text directly in a list: wrap in an item.
            node = {"type": "listItem", "content": [node]}
        self._children().append(node)

    def _text(self, text: str) -> None:
        if not text:
            return
        node: Node = {"type": "text", "text": text}
        if self.marks:
            ordered = [m for m in ("bold", "italic", "sub", "sup") if m in self.marks]
            node["marks"] = [dict(_MARK[m]) for m in ordered]
        # Merge with a previous text node that has the same marks.
        if self.inline and self.inline[-1]["type"] == "text" and self.inline[-1].get("marks") == node.get("marks"):
            self.inline[-1]["text"] += text
        else:
            self.inline.append(node)

    # ── parser callbacks ──
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag in SKIP:
                self.skip += 1
            return
        if tag in SKIP:
            self.skip += 1
            return
        if tag in AUDIO:
            if tag in ("audio", "video"):
                self.stats.audio += 1
                self.audio += 1
            return
        a = {k: (v or "") for k, v in attrs}
        if len(self.stack) > MAX_DEPTH:
            return
        if tag == "br":
            if self.inline:
                self.inline.append({"type": "hardBreak"})
        elif tag == "hr":
            self._flush()
        elif tag == "img":
            self._image(a)
        elif tag in BOLD:
            self.marks.append("bold")
        elif tag in ITALIC:
            self.marks.append("italic")
        elif tag == "sub":
            self.marks.append("sub")
        elif tag == "sup":
            self.marks.append("sup")
        elif tag in HEADINGS:
            self._flush()
            self.heading = HEADINGS[tag]
        elif tag in BLOCK_TAGS or tag == "caption":
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            attrs2: dict[str, Any] = {"ordered": tag == "ol", "start": _int(a.get("start"), 1)}
            self.stack.append(("list", [], attrs2))
        elif tag == "li":
            self._flush()
            self.stack.append(("item", [], {}))
        elif tag == "table":
            self._flush()
            self.stack.append(("table", [], {}))
        elif tag == "tr":
            self._flush()
            if self.stack[-1][0] != "table":
                self.stack.append(("table", [], {}))
            self.stack.append(("row", [], {}))
        elif tag in ("td", "th"):
            self._flush()
            if self.stack[-1][0] != "row":
                if self.stack[-1][0] != "table":
                    self.stack.append(("table", [], {}))
                self.stack.append(("row", [], {}))
            self.stack.append(
                (
                    "cell",
                    [],
                    {"header": tag == "th", "colspan": _int(a.get("colspan"), 1), "rowspan": _int(a.get("rowspan"), 1)},
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in SKIP:
                self.skip -= 1
            return
        if tag in ("audio", "video") and self.audio:
            self.audio -= 1
            return
        if tag in BOLD:
            _pop(self.marks, "bold")
        elif tag in ITALIC:
            _pop(self.marks, "italic")
        elif tag in ("sub", "sup"):
            _pop(self.marks, tag)
        elif tag in HEADINGS:
            if self.heading is not None:
                self._flush("heading", {"level": self.heading})
                self.heading = None
        elif tag in BLOCK_TAGS or tag == "caption":
            self._flush()
        elif tag in ("ul", "ol"):
            self._close("list")
        elif tag == "li":
            self._close("item")
        elif tag in ("td", "th"):
            self._close("cell")
        elif tag == "tr":
            self._close("row")
        elif tag == "table":
            self._close("table")

    def handle_data(self, data: str) -> None:
        if self.skip or self.audio:
            return
        data = SOUND.sub(self._sound, data)
        text = _WS.sub(" ", data)
        if not text:
            return
        if not self.inline and not text.strip():
            return
        self._text(text)

    def _sound(self, _m: re.Match[str]) -> str:
        self.stats.audio += 1
        return ""

    def _image(self, a: dict[str, str]) -> None:
        src = a.get("src", "")
        if not src or src.startswith(("data:", "http:", "https:", "//")):
            # Remote and inline images are never fetched or kept: no tracking pixels, no mixed content.
            self.stats.images_missing += 1
            return
        media_id = self.resolve(src) if self.resolve else None
        if media_id is None:
            self.stats.images_missing += 1
            return
        self._flush()
        self.stats.images.append(media_id)
        self._add({"type": "figure", "attrs": {"mediaId": media_id}})

    # ── structure ──
    def _close(self, kind: str) -> bool:
        # Close the innermost container of this kind (and anything left open inside it).
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == kind:
                break
        else:
            return False
        while len(self.stack) - 1 >= i and self.stack[-1][0] != kind:
            self._seal(self.stack[-1][0])
        self._seal(kind)
        return True

    def _seal(self, kind: str) -> None:
        self._flush()
        k, children, attrs = self.stack.pop()
        node: Node | None = None
        if k == "item":
            node = {"type": "listItem", "content": children or [{"type": "paragraph"}]}
        elif k == "list":
            items = [c if c["type"] == "listItem" else {"type": "listItem", "content": [c]} for c in children]
            if items:
                node = (
                    {"type": "orderedList", "attrs": {"start": attrs["start"], "type": None}, "content": items}
                    if attrs["ordered"]
                    else {"type": "bulletList", "content": items}
                )
        elif k == "cell":
            node = {
                "type": "tableHeader" if attrs["header"] else "tableCell",
                "attrs": {"colspan": attrs["colspan"], "rowspan": attrs["rowspan"], "colwidth": None, "align": None},
                "content": [c for c in children if c["type"] != "figure"] or [{"type": "paragraph"}],
            }
        elif k == "row":
            node = {"type": "tableRow", "content": children} if children else None
        elif k == "table":
            node = {"type": "table", "attrs": {"highYield": False}, "content": children} if children else None
        if node is not None:
            self._add(node)

    def finish(self) -> list[Node]:
        self.close()
        while len(self.stack) > 1:
            self._seal(self.stack[-1][0])
        self._flush()
        return self.blocks


def _int(v: str | None, default: int) -> int:
    try:
        return max(1, min(int(v or default), 50))
    except ValueError:
        return default


def _pop(marks: list[str], m: str) -> None:
    if m in marks:
        marks.remove(m)


def _ltrim(n: Node) -> Node:
    if n["type"] == "text":
        n["text"] = n["text"].lstrip()
    return n


def _rtrim(n: Node) -> Node:
    if n["type"] == "text":
        n["text"] = n["text"].rstrip()
    return n


def html_to_doc(markup: str, resolve: ImageResolver | None = None, stats: Stats | None = None) -> Node:
    """Cleaned TipTap document of an Anki field. `resolve(src)` maps an `<img src>` to a media id (or None)."""
    st = stats or Stats()
    b = _Builder(resolve, st)
    b.feed(markup)
    blocks = b.finish()
    return {"type": "doc", "content": _prune(blocks)}


def _prune(nodes: list[Node]) -> list[Node]:
    out: list[Node] = []
    for n in nodes:
        if n["type"] == "text" and not n.get("text"):
            continue
        if "content" in n:
            n["content"] = _prune(n["content"])
            if not n["content"] and n["type"] in ("bulletList", "orderedList", "table", "tableRow"):
                continue
        out.append(n)
    return out


# ── document -> HTML (cards store the cleaned form; export writes it into the .apkg) ──


def _marks_html(text: str, marks: list[dict[str, Any]] | None) -> str:
    out = htmllib.escape(text, quote=False)
    for m in marks or []:
        tag = {"bold": "b", "italic": "i", "subscript": "sub", "superscript": "sup"}.get(m.get("type", ""))
        if tag:
            out = f"<{tag}>{out}</{tag}>"
    return out


def doc_to_html(doc: Node, image_src: Callable[[str], str | None] | None = None) -> str:
    """HTML of a card document. `image_src(mediaId)` gives the `<img src>`; None drops the picture."""

    def inline(nodes: list[Node]) -> str:
        parts = []
        for n in nodes:
            if n["type"] == "text":
                parts.append(_marks_html(n.get("text", ""), n.get("marks")))
            elif n["type"] == "hardBreak":
                parts.append("<br>")
        return "".join(parts)

    def block(n: Node) -> str:
        t = n["type"]
        c = n.get("content", [])
        if t == "paragraph":
            return f"<div>{inline(c)}</div>"
        if t == "heading":
            level = min(max(int(n.get("attrs", {}).get("level", 3)), 2), 4)
            return f"<h{level}>{inline(c)}</h{level}>"
        if t == "bulletList":
            return "<ul>" + "".join(block(i) for i in c) + "</ul>"
        if t == "orderedList":
            return "<ol>" + "".join(block(i) for i in c) + "</ol>"
        if t == "listItem":
            return "<li>" + "".join(block(i) for i in c) + "</li>"
        if t == "table":
            return "<table>" + "".join(block(i) for i in c) + "</table>"
        if t == "tableRow":
            return "<tr>" + "".join(block(i) for i in c) + "</tr>"
        if t in ("tableCell", "tableHeader"):
            tag = "th" if t == "tableHeader" else "td"
            a = n.get("attrs", {})
            span = "".join(f' {k}="{int(a[k])}"' for k in ("colspan", "rowspan") if int(a.get(k, 1)) > 1)
            return f"<{tag}{span}>" + "".join(block(i) for i in c) + f"</{tag}>"
        if t == "figure":
            src = image_src(n.get("attrs", {}).get("mediaId", "")) if image_src else None
            return f'<img src="{htmllib.escape(src, quote=True)}">' if src else ""
        return ""

    return "".join(block(n) for n in doc.get("content", []))


def doc_text(doc: Node) -> str:
    """Plain text of a document (for tests and the report)."""
    out: list[str] = []

    def walk(n: Node) -> None:
        if n["type"] == "text":
            out.append(n.get("text", ""))
        elif n["type"] == "hardBreak":
            out.append("\n")
        for c in n.get("content", []):
            walk(c)
        if n["type"] in ("paragraph", "heading", "listItem", "tableRow"):
            out.append("\n")

    walk(doc)
    return re.sub(r"\n{2,}", "\n", "".join(out)).strip()


def referenced_images(markup: str) -> list[str]:
    """`<img src>` names of a field, in order (local names only)."""
    names: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "img":
                src = dict(attrs).get("src") or ""
                if src and not src.startswith(("data:", "http:", "https:", "//")):
                    names.append(htmllib.unescape(src))

    p = P(convert_charrefs=True)
    p.feed(markup)
    p.close()
    return names
