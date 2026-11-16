"""Mock exam answer keys are never content.

The mock chapters' "Instructions" sections carry the answer key as a table (header cells `Q`, `Answer`, `Topic`,
repeated per column group). The answers already live in the questions' backend-only columns, so the importer
removes such tables from `mock_exam` sections: no reader, search snippet or assistant context can show a key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TABLE_HTML = re.compile(r"<table\b.*?</table>", re.S | re.I)
_TH = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def is_answer_key_cells(cells: list[str]) -> bool:
    return "q" in cells and "answer" in cells


def _html_cells(table_html: str) -> list[str]:
    return [_TAG.sub("", c).strip().lower() for c in _TH.findall(table_html)]


def _text(node: Mapping[str, Any]) -> str:
    if node.get("type") == "text":
        return str(node.get("text") or "")
    return "".join(_text(c) for c in node.get("content") or [] if isinstance(c, Mapping))


def _is_key_table(node: Mapping[str, Any]) -> bool:
    if node.get("type") != "table":
        return False
    rows = [r for r in node.get("content") or [] if isinstance(r, Mapping)]
    if not rows:
        return False
    first = [_text(c).strip().lower() for c in rows[0].get("content") or [] if isinstance(c, Mapping)]
    return is_answer_key_cells(first)


def _strip_node(node: Any) -> Any:
    if not isinstance(node, Mapping):
        return node
    kids = node.get("content")
    if not isinstance(kids, list):
        return node
    kept = [_strip_node(k) for k in kids if not (isinstance(k, Mapping) and _is_key_table(k))]
    return {**node, "content": kept}


def strip_answer_key(body: Mapping[str, Any] | None, body_html: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The section body and HTML without any answer-key table."""
    new_body = dict(_strip_node(body)) if body is not None else None
    new_html = body_html
    if body_html:
        new_html = _TABLE_HTML.sub(
            lambda m: "" if is_answer_key_cells(_html_cells(m.group(0))) else m.group(0), body_html
        )
    return new_body, new_html
