"""Pathway text without internal ids, due-rule tokens and empty table cells; mock instructions without a key pointer."""

from __future__ import annotations

from naadi_api.embeddings.sources import clean_pathway_text
from naadi_api.embeddings.text import markdown_to_text
from naadi_api.seed.mock import strip_answer_key

GUIDE = """## California

| ID | Task | Due | Cost | Source |
|---|---|---|---|---|
| ca-07 | Get your California PTL on time | 150 days after residency start (residencystart+150d) | - | official |
| ca-08 | Know that training outside California still works | (erassubmit-60d) | $0 | official |
| ca-10 | Do not wait | immediately (start) | $0 | — |

California is a J-1 heavy market.
"""


def test_ids_due_rules_and_empty_cells_are_removed() -> None:
    out = clean_pathway_text(markdown_to_text(GUIDE))
    assert "ca-07" not in out and "residencystart+150d" not in out and "erassubmit-60d" not in out
    assert "(start)" not in out and "official" not in out and "—" not in out
    assert "; ;" not in out and not any(line.startswith(";") or line.endswith(";") for line in out.splitlines())
    assert "Get your California PTL on time ; 150 days after residency start" in out
    assert "California is a J-1 heavy market." in out


def test_a_repeated_title_line_is_dropped() -> None:
    assert clean_pathway_text("Title\nBody", "Title") == "Body"


def test_mock_instructions_no_longer_point_at_a_key() -> None:
    body = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Write answers (1-80), then score yourself against the answer key at the end.",
                    }
                ],
            }
        ],
    }
    new_body, html = strip_answer_key(body, "<p>then score yourself against the answer key at the end</p>")
    assert "answer key" not in str(new_body) and "answer key" not in (html or "")
    assert "your results appear after the exam" in str(new_body)
