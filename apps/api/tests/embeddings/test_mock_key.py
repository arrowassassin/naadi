"""Mock answer keys are stripped from section bodies (unit; tests/db/test_seed_mock.py covers the import)."""

from __future__ import annotations

from typing import Any

from naadi_api.seed.mock import strip_answer_key


def cell(kind: str, text: str) -> dict[str, Any]:
    return {"type": kind, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def row(kind: str, *texts: str) -> dict[str, Any]:
    return {"type": "tableRow", "content": [cell(kind, t) for t in texts]}


KEY = {"type": "table", "content": [row("tableHeader", "Q", "Answer", "Topic"), row("tableCell", "1", "B", "Renal")]}
OTHER = {"type": "table", "content": [row("tableHeader", "Score", "Meaning"), row("tableCell", "70%", "ok")]}


def test_the_key_table_is_removed_and_other_tables_stay() -> None:
    body = {"type": "doc", "content": [{"type": "paragraph"}, {"type": "callout", "content": [KEY]}, KEY, OTHER]}
    html = (
        "<p>x</p><table><thead><tr><th>Q</th><th>Answer</th><th>Topic</th></tr></thead><tbody><tr><td>1</td>"
        "<td>B</td><td>Renal</td></tr></tbody></table><table><tr><th>Score</th><th>Meaning</th></tr></table>"
    )
    out_body, out_html = strip_answer_key(body, html)
    assert out_body is not None and out_html is not None
    assert "Answer" not in str(out_body) and "Renal" not in str(out_body)
    assert "Score" in str(out_body)
    assert "Answer" not in out_html and "Renal" not in out_html and "Score" in out_html and "<p>x</p>" in out_html
    assert strip_answer_key(None, None) == (None, None)
