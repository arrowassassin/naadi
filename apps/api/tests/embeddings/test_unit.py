"""Unit tests (no database, no model): text extraction, chunking, fusion, snippets, query terms, and the
guard that no question column other than stem and options can reach the index."""

from __future__ import annotations

import inspect
import re

import pytest

from naadi_api.embeddings import ranking, sources
from naadi_api.embeddings.chunker import MAX_TOKENS, chunk_text, estimate_tokens
from naadi_api.embeddings.engine import EMBEDDING_DIM, spec_from_config, vector_literal
from naadi_api.embeddings.query import Candidate, order, relevant, select
from naadi_api.embeddings.text import html_to_text, markdown_to_text, tiptap_to_text
from tests.conftest import REPO_ROOT


def para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


# ── text ──


def test_tiptap_text_walks_blocks_lists_tables_and_callouts() -> None:
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Types of RTA"}]},
            {
                "type": "callout",
                "attrs": {"variant": "bridge"},
                "content": [
                    {
                        "type": "bulletList",
                        "content": [
                            {"type": "listItem", "content": [para("Type 1 is distal")]},
                            {"type": "listItem", "content": [para("Type 4 is hyperkalemic")]},
                        ],
                    }
                ],
            },
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableHeader", "content": [para("Type")]},
                            {"type": "tableHeader", "content": [para("Potassium")]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [para("4")]},
                            {"type": "tableCell", "content": [para("High")]},
                        ],
                    },
                ],
            },
        ],
    }
    assert tiptap_to_text(doc).splitlines() == [
        "Types of RTA",
        "Type 1 is distal",
        "Type 4 is hyperkalemic",
        "Type ; Potassium",
        "4 ; High",
    ]


def test_tiptap_figures_contribute_only_approved_captions() -> None:
    doc = {
        "type": "doc",
        "content": [
            para("Before"),
            {"type": "figure", "attrs": {"mediaId": "fig-ok"}},
            {"type": "figure", "attrs": {"mediaId": "fig-hidden"}},
        ],
    }
    assert tiptap_to_text(doc, {"fig-ok": "Figure 1. Tubule"}).splitlines() == ["Before", "Figure: Figure 1. Tubule"]
    assert tiptap_to_text(None) == ""
    assert tiptap_to_text({"type": "doc"}) == ""


def test_html_and_markdown_to_plain_text() -> None:
    assert html_to_text("<p>A &amp; B</p><p>Second<br>line</p>") == "A & B\nSecond\nline"
    assert html_to_text(None) == ""
    md = "## Step 1\n\n- Pay the **fee** [ECFMG](https://ecfmg.org/x)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    out = markdown_to_text(md)
    assert "Pay the fee ECFMG" in out
    assert "https" not in out and "**" not in out and "---" not in out
    assert "1 ; 2" in out.replace("  ", " ")


# ── chunker ──


def test_chunks_respect_the_token_cap_and_keep_the_heading() -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * 90 for i in range(12)]
    chunks = chunk_text("\n".join(paragraphs), heading="Renal tubular acidosis")
    assert len(chunks) > 3
    for c in chunks:
        assert c.startswith("Renal tubular acidosis\n")
        assert estimate_tokens(c) <= MAX_TOKENS + 10
    joined = "\n".join(chunks)
    assert all(f"Paragraph {i}." in joined for i in range(12))  # nothing is lost


def test_one_huge_paragraph_is_split_at_sentences() -> None:
    text = " ".join(f"Sentence number {i} says something about the kidney." for i in range(120))
    chunks = chunk_text(text)
    assert len(chunks) >= 2
    assert all(estimate_tokens(c) <= MAX_TOKENS for c in chunks)
    assert "Sentence number 119" in chunks[-1]


def test_a_short_tail_joins_the_previous_chunk_and_short_text_is_one_chunk() -> None:
    assert chunk_text("Just one line.") == ["Just one line."]
    assert chunk_text("   \n  ") == []
    blocks = ["a " * 190, "b " * 190, "tail words here"]
    chunks = chunk_text("\n".join(blocks))
    assert chunks[-1].rstrip().endswith("tail words here")
    assert len(chunks) == 2


# ── ranking ──


def test_query_terms_drop_stop_words_but_keep_something() -> None:
    assert ranking.query_terms("What is the anion gap in DKA?") == ["anion", "gap", "dka"]
    assert ranking.query_terms("the and of") == ["the", "and", "of"]
    assert ranking.query_terms("type 4 RTA") == ["type", "4", "rta"]


def test_tsquery_is_an_or_query_with_a_prefix_on_the_last_term() -> None:
    assert ranking.tsquery(["anion", "gap", "dka"]) == "anion | gap | dka:*"
    assert ranking.tsquery(["a1"], prefix_last=False) == "a1"
    assert ranking.tsquery([]) is None
    # nothing that could be tsquery syntax survives
    assert ranking.tsquery(["x'; drop", "ok"]) == "ok:*" or ranking.tsquery(["x'; drop", "ok"]) == "ok"


def test_rrf_rewards_agreement_and_ignores_duplicates() -> None:
    fused = ranking.rrf([["a", "b", "c"], ["b", "a", "d"], ["b", "b"]])
    assert fused["b"] > fused["a"] > fused["c"]
    assert fused["d"] < fused["a"]
    assert ranking.rrf([]) == {}
    assert ranking.rrf([["x", "y"]], k=60)["x"] == pytest.approx(1 / 61)


def test_best_window_picks_the_sentence_with_the_terms() -> None:
    text = "Intro about nothing. More filler here. Type 4 RTA causes hyperkalemia with low aldosterone. Closing."
    win = ranking.best_window(text, ["rta", "hyperkalemia"], max_words=8)
    assert win.startswith("… Type 4 RTA causes hyperkalemia")
    assert ranking.best_window("", ["x"]) == ""


def test_highlight_marks_stems_and_returns_plain_segments() -> None:
    segs = ranking.highlight("Hyperkalemia in acidosis, not <b>acid</b>", ["hyperkalemic", "acidosis"])
    assert [s.text for s in segs if s.hit] == ["Hyperkalemia", "acidosis"]
    assert "".join(s.text for s in segs) == "Hyperkalemia in acidosis, not <b>acid</b>"  # text is untouched
    assert ranking.highlight("", ["x"]) == []
    assert [s.hit for s in ranking.highlight("plain", [])] == [False]


def test_drop_heading_removes_only_the_first_line_when_it_is_the_heading() -> None:
    assert ranking.drop_heading("Heading\nbody text", "Heading") == "body text"
    assert ranking.drop_heading("Other\nbody", "Heading") == "Other\nbody"
    assert ranking.drop_heading("Heading", "Heading") == "Heading"


def cand(i: int, ref_type: str, fused: float, rerank: float | None = None) -> Candidate:
    return Candidate(i, ref_type, f"r{i}", 0, "text", fused=fused, rerank=rerank)


def test_order_puts_the_reranked_head_first_then_fused_order() -> None:
    cs = [cand(1, "section", 0.9, rerank=-2.0), cand(2, "section", 0.8, rerank=3.0), cand(3, "section", 0.7)]
    assert [c.id for c in order(cs)] == [2, 1, 3]


def test_select_caps_each_group_when_searching_everything() -> None:
    cs = [cand(i, "question", 1 - i / 100) for i in range(10)] + [cand(20, "section", 0.1), cand(21, "note", 0.05)]
    chosen = select(cs, "all", 24)
    assert sum(1 for c in chosen if c.ref_type == "question") == ranking.PER_GROUP
    assert {c.ref_type for c in chosen} == {"question", "section", "note"}
    assert len(select(cs, "questions", 3)) == 3


# ── what the index can contain ──


def test_question_text_is_stem_and_options_only() -> None:
    text = sources.question_text(
        "<p>A boy has <b>X</b>.</p>", [{"label": "A", "html": "Alpha"}, {"label": "B", "html": "Beta"}]
    )
    assert text == "A boy has X.\nA. Alpha\nB. Beta"
    assert sources.question_text("<p>s</p>", None) == "s"


def test_no_source_query_touches_an_answer_column() -> None:
    """The loaders select questions by an explicit column list; none of the answer-bearing columns may appear."""
    src = inspect.getsource(sources)
    forbidden = ("answer", "explanation", "why_not", "educational_objective", "review", "stats")
    sql_literals = re.findall(r'"((?:select|insert)[^"]*)"', src)
    assert sql_literals
    for lit in sql_literals:
        assert not any(f in lit.lower() for f in forbidden), lit
    q_src = inspect.getsource(sources.question_docs)
    assert "q.id, q.stem_html, q.options" in q_src


def test_engine_spec_comes_from_models_yaml_and_matches_the_column() -> None:
    spec = spec_from_config(REPO_ROOT / "config")
    assert spec.dimensions == EMBEDDING_DIM == 384
    assert spec.model and spec.reranker
    assert vector_literal([0.5, -1.0, 0.123456789]) == "[0.5,-1,0.123457]"


def scored(i: int, rerank: float | None, *, lexical: bool = False, cosine: float | None = None) -> Candidate:
    c = cand(i, "section", 1 - i / 100, rerank=rerank)
    c.lexical, c.cosine = lexical, cosine
    return c


def test_relevance_floor_makes_nothing_found_possible() -> None:
    """Vector search always returns neighbours; unrelated queries score far below the floor."""
    assert relevant([scored(1, -9.1), scored(2, -10.4), scored(3, None, cosine=0.7)]) == []
    kept = relevant([scored(1, -9.0), scored(2, 1.5), scored(3, -2.0)])
    assert [c.id for c in kept] == [2, 3]


def test_tail_needs_a_text_match_or_a_close_vector_and_a_relevant_head() -> None:
    cs = [
        scored(1, 2.0),
        scored(2, None, lexical=True, cosine=0.4),
        scored(3, None, cosine=0.7),
        scored(4, None, cosine=0.5),
    ]
    assert [c.id for c in relevant(cs)] == [1, 2, 3]
    assert [c.id for c in relevant([scored(1, -9.0), scored(2, None, lexical=True)])] == []
    assert ranking.RERANK_FLOOR < ranking.WEAK_RERANK < 0
