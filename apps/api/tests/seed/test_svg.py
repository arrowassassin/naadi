"""SVG sanitiser: safe, well-formed, case-correct output for every seed figure and for hostile input."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from naadi_api.seed.svg import SvgError, sanitize_svg

FIGURES = Path(__file__).resolve().parents[4] / "data" / "figures"
NS = "{http://www.w3.org/2000/svg}"


def _root(data: bytes) -> ET.Element:
    return ET.fromstring(data)  # noqa: S314 - parsing our own sanitiser output in a test


def test_every_seed_figure_becomes_well_formed_svg_with_camel_case_attributes() -> None:
    files = sorted(FIGURES.glob("*.svg"))
    assert len(files) == 95
    for f in files:
        raw = f.read_bytes()
        s = sanitize_svg(raw, label="caption")
        root = _root(s.data)
        assert root.tag == f"{NS}svg", f.name
        assert root.get("viewBox"), f.name
        assert root.get("viewbox") is None
        assert root.get("role") == "img"
        assert root.get("aria-label")
        assert s.width and s.height
        assert s.removed == [], (f.name, s.removed)
        for marker in root.iter(f"{NS}marker"):
            assert marker.get("markerWidth") and marker.get("refX"), f.name
        # no element lost: every source start tag survives (comments are not elements)
        assert len(re.findall(rb"<[a-zA-Z]", raw)) == sum(1 for _ in root.iter()), f.name
        assert b"<script" not in s.data.lower()


def test_text_survives_including_html_italics() -> None:
    raw = (FIGURES / "ch10-fig06.svg").read_bytes()
    s = sanitize_svg(raw, label="x")
    root = _root(s.data)
    text = "".join(root.itertext())
    assert "C. difficile" in text
    assert any(t.get("font-style") == "italic" for t in root.iter(f"{NS}tspan"))
    assert "<i>" not in s.data.decode()


def test_duplicate_xmlns_is_repaired() -> None:
    raw = (FIGURES / "ch03-fig01.svg").read_bytes()
    with pytest.raises(ET.ParseError):
        _root(raw)
    s = sanitize_svg(raw, label="x")
    assert _root(s.data).get("viewBox")
    assert any("duplicate xmlns" in f for f in s.fixed)


def test_size_comes_from_the_viewbox() -> None:
    s = sanitize_svg((FIGURES / "ch01-fig01.svg").read_bytes(), label="x")
    assert (s.width, s.height) == (760, 420)


HOSTILE = b"""<svg xmlns="http://www.w3.org/2000/svg" viewbox="0 0 10 10" onload="alert(1)">
<script>alert(1)</script>
<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>
<a href="https://evil.example"><text x="1" y="1">link</text></a>
<image href="https://evil.example/x.png" width="1" height="1"/>
<use href="#a"/>
<style>@import url(https://evil.example/x.css);</style>
<rect width="1" height="1" fill="url(https://evil.example/#p)" style="fill:red" onclick="x()"/>
<rect width="1" height="1" fill="url(#local)" stroke="javascript:alert(1)"/>
<path d="M0 0" marker-end="url(#arrow)"/>
<text x="1" y="2">a &lt; b &amp; c</text>
</svg>"""


def test_hostile_markup_is_removed_and_reported() -> None:
    s = sanitize_svg(HOSTILE, label="Diagram")
    out = s.data.decode()
    root = _root(s.data)
    for bad in (
        "script",
        "foreignObject",
        "onload",
        "onclick",
        "evil.example",
        "javascript",
        "style",
        "<use",
        "<a ",
        "<image",
    ):
        assert bad not in out, bad
    assert 'fill="url(#local)"' in out
    assert 'marker-end="url(#arrow)"' in out
    assert "a < b & c" in "".join(root.itertext())
    assert root.get("aria-label") == "Diagram"
    joined = " | ".join(s.removed)
    for reported in ("element <script>", "element <foreignobject>", "attribute onload", "non-local reference in fill"):
        assert reported in joined, reported


def test_caption_becomes_the_label_when_missing() -> None:
    s = sanitize_svg(b'<svg viewBox="0 0 4 3"><rect width="1" height="1"/></svg>', label="A caption")
    root = _root(s.data)
    assert root.get("aria-label") == "A caption"
    assert root.get("role") == "img"


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"<html><body>x</body></html>", "no <svg>"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', "viewBox"),
        (b'<svg viewBox="0 0 1 1"></svg>', "aria-label"),
        (b"\xff\xfe", "UTF-8"),
    ],
)
def test_unusable_input_is_refused(raw: bytes, why: str) -> None:
    with pytest.raises(SvgError, match=why):
        sanitize_svg(raw, label="" if why == "aria-label" else "x")


def test_size_limit() -> None:
    with pytest.raises(SvgError, match="larger"):
        sanitize_svg(b"<svg>" + b" " * 100 + b"</svg>", label="x", max_bytes=50)
