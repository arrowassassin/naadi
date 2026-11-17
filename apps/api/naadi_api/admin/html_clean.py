"""Server-side allowlist for question HTML (review19 defect 6), the same rules as the web's SafeHtml.

Question stems, options and explanations are rendered by `apps/web/src/content/SafeHtml.tsx`, but stored items
also reach other consumers (assistant tools, exports, review). So the admin editor stores HTML that is already
clean: only the formatting tags below survive, with no attributes except `colspan`/`rowspan` (2 to 50) on table
cells. Script-like elements are dropped with their content; any other tag is unwrapped to its text. Text is
re-escaped, so entity tricks (`jav&#x61;script:`) can never become markup. Standard library only (html.parser),
like the seed's SVG sanitiser.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser

# Keep in step with SafeHtml.tsx ALLOWED / VOID / DROP (tests/admin/test_html_clean.py compares them).
ALLOWED = frozenset(
    {
        "p", "br", "b", "strong", "i", "em", "u", "sub", "sup", "small", "code",
        "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "span", "div",
    }
)  # fmt: skip
VOID = frozenset({"br"})
DROP = frozenset({"script", "style", "template", "iframe", "object", "embed", "noscript", "svg", "math"})
SPAN_ATTRS = ("colspan", "rowspan")
MAX_NODES = 5000


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []  # allowed elements currently open, in order
        self.drop_depth = 0
        self.nodes = 0

    def _budget(self) -> bool:
        self.nodes += 1
        return self.nodes <= MAX_NODES

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP:
            if tag not in VOID:
                self.drop_depth += 1
            return
        if self.drop_depth or not self._budget() or tag not in ALLOWED:
            return
        if tag in VOID:
            self.out.append(f"<{tag}>")
            return
        extra = ""
        if tag in ("td", "th"):
            for name, value in attrs:
                if name.lower() in SPAN_ATTRS and value is not None and value.strip().isdigit():
                    n = int(value.strip())
                    if 1 < n <= 50:
                        extra += f' {name.lower()}="{n}"'
        self.out.append(f"<{tag}{extra}>")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP or self.drop_depth or tag not in ALLOWED:
            return
        if tag in VOID:
            if self._budget():
                self.out.append(f"<{tag}>")
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if self.drop_depth or tag not in ALLOWED or tag in VOID or tag not in self.open:
            return
        # Close everything opened after it (mis-nested input), then the tag itself.
        while self.open:
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.drop_depth and data and self._budget():
            self.out.append(html.escape(data, quote=False))

    def close_all(self) -> str:
        self.close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def clean_html(raw: str) -> str:
    """The allowlisted form of `raw` (idempotent: cleaning clean HTML returns it unchanged)."""
    c = _Cleaner()
    c.feed(raw)
    return c.close_all().strip()


def text_of(cleaned: str) -> str:
    """Plain text of cleaned HTML (for 'is it blank' checks)."""
    c = _Cleaner()
    c.feed(cleaned)
    c.close()
    return html.unescape("".join(p for p in c.out if not p.startswith("<"))).strip()
