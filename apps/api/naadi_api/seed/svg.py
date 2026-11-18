"""SVG sanitiser for figures served from the public bucket.

The seed SVGs were written through an HTML serialiser, so attribute names are lower-cased (`viewbox`,
`markerwidth`, `refx` — which SVG, being XML, ignores) and four files repeat `xmlns` (not well-formed XML, so
a browser would refuse to render them as an image). The sanitiser therefore re-parses leniently and writes a
fresh, well-formed SVG with:

- only allow-listed elements and presentation attributes (no script, foreignObject, style, use, image, a,
  animation, event handlers, or anything that can load a URL);
- `url(...)` only as a same-document reference (`url(#id)`), on the attributes that take one;
- SVG's camelCase attribute names restored;
- the root element carrying `xmlns`, `role="img"` and an `aria-label` (the caption when missing).

Everything removed is reported, so the import report shows exactly what changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

SVG_NS = "http://www.w3.org/2000/svg"

ELEMENTS = {
    "svg",
    "g",
    "defs",
    "marker",
    "path",
    "rect",
    "line",
    "polyline",
    "polygon",
    "circle",
    "ellipse",
    "text",
    "tspan",
    "title",
    "desc",
    "lineargradient",
    "radialgradient",
    "stop",
    "clippath",
}
# HTMLParser lower-cases names; SVG is case-sensitive.
CAMEL = {
    "lineargradient": "linearGradient",
    "radialgradient": "radialGradient",
    "clippath": "clipPath",
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
    "markerwidth": "markerWidth",
    "markerheight": "markerHeight",
    "markerunits": "markerUnits",
    "refx": "refX",
    "refy": "refY",
    "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform",
    "clippathunits": "clipPathUnits",
    "textlength": "textLength",
    "lengthadjust": "lengthAdjust",
    "pathlength": "pathLength",
    "spreadmethod": "spreadMethod",
}
ATTRIBUTES = {
    "id",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "fx",
    "fy",
    "dx",
    "dy",
    "width",
    "height",
    "d",
    "points",
    "transform",
    "viewbox",
    "preserveaspectratio",
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-width",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-opacity",
    "stroke-miterlimit",
    "opacity",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "letter-spacing",
    "text-anchor",
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "text-decoration",
    "marker-start",
    "marker-mid",
    "marker-end",
    "markerwidth",
    "markerheight",
    "markerunits",
    "refx",
    "refy",
    "orient",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientunits",
    "gradienttransform",
    "clip-path",
    "clippathunits",
    "textlength",
    "lengthadjust",
    "pathlength",
    "spreadmethod",
    "role",
    "aria-label",
    "aria-hidden",
    "xmlns",
}
URL_ATTRIBUTES = {"fill", "stroke", "marker-start", "marker-mid", "marker-end", "clip-path"}
LOCAL_URL = re.compile(r"^url\(\s*#[A-Za-z][\w.-]*\s*\)$")
# Anything that could reach outside the document or run code, whatever the attribute.
DANGEROUS_VALUE = re.compile(r"(javascript|data|vbscript)\s*:|url\s*\(|expression\s*\(|@import", re.IGNORECASE)
# HTML inline formatting inside SVG text (e.g. `<text><i>C. difficile</i></text>`) becomes a styled tspan.
INLINE_FORMATTING = {
    "i": ("font-style", "italic"),
    "em": ("font-style", "italic"),
    "b": ("font-weight", "bold"),
    "strong": ("font-weight", "bold"),
}
TEXT_ELEMENTS = {"text", "tspan"}
VIEWBOX = re.compile(r"^\s*(-?[\d.]+)[\s,]+(-?[\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*$")


class SvgError(ValueError):
    pass


@dataclass
class SanitizedSvg:
    data: bytes
    width: int | None
    height: int | None
    removed: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)


@dataclass
class _Node:
    tag: str
    attrs: list[tuple[str, str]]
    children: list[_Node | str] = field(default_factory=list)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: _Node | None = None
        self.stack: list[_Node] = []
        self.skip_depth = 0  # inside a dropped element
        self.removed: list[str] = []
        self.fixed: list[str] = []
        self._inline_open: list[int] = []  # stack depths of tspans made from <i>/<b>

    def _open(self, tag: str, raw_attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        if self.skip_depth:
            if not self_closing:
                self.skip_depth += 1
            return
        if tag in INLINE_FORMATTING and self.stack and self.stack[-1].tag in TEXT_ELEMENTS and not raw_attrs:
            self.fixed.append(f"<{tag}> in text -> styled <tspan>")
            span = _Node("tspan", [INLINE_FORMATTING[tag]])
            self.stack[-1].children.append(span)
            if not self_closing:
                self.stack.append(span)
                self._inline_open.append(len(self.stack))
            return
        if tag not in ELEMENTS or (tag == "svg" and self.root is not None):
            self.removed.append(f"element <{tag}>")
            if not self_closing:
                self.skip_depth = 1
            return
        attrs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, value in raw_attrs:
            v = (value or "").strip()
            if name in seen:
                self.fixed.append(f"duplicate {name} on <{tag}>")
                continue
            seen.add(name)
            if name not in ATTRIBUTES or name.startswith("on"):
                self.removed.append(f"attribute {name} on <{tag}>")
                continue
            if name == "xmlns":
                if v != SVG_NS:
                    self.removed.append(f"xmlns={v!r}")
                continue  # written once on the root
            if name in URL_ATTRIBUTES and v.lower().startswith("url"):
                if not LOCAL_URL.match(v):
                    self.removed.append(f"non-local reference in {name} on <{tag}>")
                    continue
            elif DANGEROUS_VALUE.search(v):
                self.removed.append(f"unsafe value in {name} on <{tag}>")
                continue
            if name in CAMEL:
                self.fixed.append(f"{name} -> {CAMEL[name]}")
            attrs.append((CAMEL.get(name, name), v))
        node = _Node(CAMEL.get(tag, tag), attrs)
        if self.stack:
            self.stack[-1].children.append(node)
        elif self.root is None:
            self.root = node
        if not self_closing:
            self.stack.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in INLINE_FORMATTING and self._inline_open:
            depth = self._inline_open.pop()
            del self.stack[depth - 1 :]
            return
        want = CAMEL.get(tag, tag)
        # Close up to the matching element (tolerates the HTML serialiser's unclosed tags).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == want:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.stack:
            return
        if self.stack[-1].tag in {"text", "tspan", "title", "desc"}:
            self.stack[-1].children.append(data)
        elif data.strip():
            self.removed.append(f"stray text in <{self.stack[-1].tag}>")
        else:
            self.stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        pass

    def handle_decl(self, decl: str) -> None:
        self.removed.append("doctype declaration")

    def handle_pi(self, data: str) -> None:
        if not data.lower().startswith("xml "):
            self.removed.append("processing instruction")

    def unknown_decl(self, data: str) -> None:
        self.removed.append("CDATA/declaration")


def _write(node: _Node, out: list[str]) -> None:
    out.append(f"<{node.tag}")
    for name, value in node.attrs:
        out.append(f' {name}="{escape(value, quote=True)}"')
    if not node.children:
        out.append("/>")
        return
    out.append(">")
    for child in node.children:
        if isinstance(child, str):
            out.append(escape(child, quote=False))
        else:
            _write(child, out)
    out.append(f"</{node.tag}>")


def _size(root: _Node) -> tuple[int | None, int | None]:
    attrs = dict(root.attrs)
    m = VIEWBOX.match(attrs.get("viewBox", ""))
    if m:
        w, h = float(m.group(3)), float(m.group(4))
        if w > 0 and h > 0:
            return round(w), round(h)
    try:
        w, h = float(attrs["width"].removesuffix("px")), float(attrs["height"].removesuffix("px"))
    except (KeyError, ValueError):
        return None, None
    return (round(w), round(h)) if w > 0 and h > 0 else (None, None)


def sanitize_svg(raw: bytes, *, label: str | None = None, max_bytes: int = 2 * 1024 * 1024) -> SanitizedSvg:
    """Return a safe, well-formed SVG. Raises SvgError when the input is not a usable SVG."""
    if len(raw) > max_bytes:
        raise SvgError(f"SVG is larger than {max_bytes} bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SvgError("SVG is not UTF-8") from exc
    b = _Builder()
    b.feed(text)
    b.close()
    root = b.root
    if root is None or root.tag != "svg":
        raise SvgError("no <svg> root element")
    attrs = [(n, v) for n, v in root.attrs if n not in {"role", "aria-label"}]
    current = dict(root.attrs)
    aria = current.get("aria-label") or (label or "").strip()
    if not aria:
        raise SvgError("SVG has no aria-label and no caption to use")
    root.attrs = [("xmlns", SVG_NS), ("role", "img"), ("aria-label", aria[:1000]), *attrs]
    width, height = _size(root)
    if width is None:
        raise SvgError("SVG has no usable viewBox or width/height")
    out: list[str] = []
    _write(root, out)
    return SanitizedSvg("".join(out).encode("utf-8"), width, height, removed=b.removed, fixed=sorted(set(b.fixed)))
