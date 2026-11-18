"""review19 defect 6: question HTML is stored through the same allowlist the web's SafeHtml renders with."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from naadi_api.admin.html_clean import ALLOWED, DROP, VOID, clean_html, text_of

SAFE_HTML = (Path(__file__).resolve().parents[4] / "apps/web/src/content/SafeHtml.tsx").read_text(encoding="utf-8")


def _ts_set(name: str) -> set[str]:
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", SAFE_HTML, re.S)
    assert m, name
    return set(re.findall(r'"([a-z0-9]+)"', m.group(1)))


def test_lists_match_safehtml() -> None:
    assert set(ALLOWED) == _ts_set("ALLOWED")
    assert set(VOID) == _ts_set("VOID")
    assert set(DROP) == _ts_set("DROP")


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<img/src=x/onerror=alert(1)>", ""),
        ("<svg/onload=alert(1)>", ""),
        ('<a href="jav&#x61;script:alert(1)">x</a>', "x"),
        ('<a href="data:text/html,<script>alert(1)</script>">y</a>', "y"),
        ("<p onclick='x()' style='color:red'>Hi <b>there</b></p>", "<p>Hi <b>there</b></p>"),
        ("<script>alert(1)</script><p>ok</p>", "<p>ok</p>"),
        ("<style>p{}</style>text", "text"),
        ("<p>1 &lt; 2 &amp; 3</p>", "<p>1 &lt; 2 &amp; 3</p>"),
        ("<p>&lt;script&gt;</p>", "<p>&lt;script&gt;</p>"),
        (
            '<table><tr><td colspan="2" onclick="x">a</td><td rowspan="99">b</td></tr></table>',
            '<table><tr><td colspan="2">a</td><td>b</td></tr></table>',
        ),
        ("<p>unclosed <b>bold", "<p>unclosed <b>bold</b></p>"),
        ("line<br/>break<br>", "line<br>break<br>"),
        ("<iframe src=x>inner</iframe>after", "after"),
        ("<math><mi>x</mi></math>y", "y"),
    ],
)
def test_clean(raw: str, clean: str) -> None:
    out = clean_html(raw)
    assert out == clean
    assert clean_html(out) == out  # idempotent
    assert "javascript" not in out.lower() and "onerror" not in out and "<img" not in out


def test_text_of() -> None:
    assert text_of(clean_html("<p>A &amp; B</p>")) == "A & B"
    assert text_of(clean_html("<script>x</script>")) == ""
