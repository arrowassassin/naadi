"""Held-out pick (deterministic, exactly 100) and its exclusion from retrieval and page contexts."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest
from model_eval.answerers import LocalAnswerer
from model_eval.heldout import HELDOUT_SIZE, Question, load_questions, pick_heldout
from model_eval.prompts import load_prompts
from model_eval.retrieval import HashingEmbedder, HeldoutFilter, Index, build_corpus, is_answer_key, shingles

from .conftest import DATA, PROMPTS


def test_pick_is_the_lowest_100_sha256_ids(questions: list[Question], heldout: list[Question]) -> None:
    assert len(heldout) == HELDOUT_SIZE == 100
    assert len({q.id for q in heldout}) == 100
    expected = sorted((hashlib.sha256(q.id.encode("utf-8")).hexdigest(), q.id) for q in questions)[:100]
    assert [q.id for q in heldout] == [qid for _, qid in expected]


def test_pick_does_not_depend_on_file_order(questions: list[Question], heldout: list[Question]) -> None:
    shuffled = list(questions)
    random.Random(7).shuffle(shuffled)  # noqa: S311 - a test shuffle, not cryptography
    assert [q.id for q in pick_heldout(shuffled)] == [q.id for q in heldout]


def test_pick_needs_enough_questions(questions: list[Question]) -> None:
    with pytest.raises(ValueError, match="at least 100"):
        pick_heldout(questions[:99])


def test_loader_rejects_an_answer_that_is_not_an_option(tmp_path: Path) -> None:
    raw = json.loads((DATA / "questions.json").read_text())[:1]
    raw[0]["answer"] = "Z"
    p = tmp_path / "q.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="not one of its option labels"):
        load_questions(p)


def test_no_retrieval_chunk_overlaps_a_heldout_question(
    heldout: list[Question], sections: list[tuple[str, str, str, str]]
) -> None:
    hidden = HeldoutFilter(t for q in heldout for t in q.hidden_texts())
    chunks, dropped = build_corpus(sections, hidden)
    assert len(chunks) > 300
    hidden_sh = set().union(*(shingles(t) for q in heldout for t in q.hidden_texts()))
    for c in chunks:
        assert shingles(c.text).isdisjoint(hidden_sh), c.id
        assert not is_answer_key(c.text), c.id
    # mock1's instructions hold the answer key of its 80 questions (some are held out): never indexed.
    assert dropped["answer_key"] >= 1
    assert not any(c.section_id == "mock1-s01" and " B Hematology" in c.text for c in chunks)


def test_answer_key_detector() -> None:
    assert is_answer_key("Q Answer Topic 1 B Biostatistics: sens 2 D Cardiology: HCM 3 A Renal: RTA")
    assert not is_answer_key("Type 1 RTA has urine pH above 5.5; type 4 has hyperkalemia. Step 2 CK asks why.")


def test_a_section_quoting_a_heldout_question_is_dropped(heldout: list[Question]) -> None:
    q = heldout[0]
    hidden = HeldoutFilter(t for h in heldout for t in h.hidden_texts())
    sections = [
        ("chX", "chX-s01", "Leaky section", f"Some intro text. {q.stem} More text after it."),
        ("chX", "chX-s02", "Clean section", "Nephrons filter plasma and reabsorb most of the filtered sodium."),
    ]
    chunks, dropped = build_corpus(sections, hidden)
    assert dropped == {"heldout_overlap": 1, "answer_key": 0}
    assert [c.section_id for c in chunks] == ["chX-s02"]
    explanation_only = [("chX", "chX-s03", "Explained", f"Background. {q.explanation}")]
    assert build_corpus(explanation_only, hidden) == ([], {"heldout_overlap": 1, "answer_key": 0})


def test_mcq_context_never_contains_heldout_text(
    heldout: list[Question], sections: list[tuple[str, str, str, str]]
) -> None:
    hidden = HeldoutFilter(t for q in heldout for t in q.hidden_texts())
    chunks, _ = build_corpus(sections, hidden)
    index = Index(chunks, HashingEmbedder())
    answerer = LocalAnswerer(gateway=None, index=index)  # type: ignore[arg-type]  # retrieval only
    for q in heldout[:25]:
        hits = index.search(q.stem + " " + " ".join(t for _, t in q.options), 4)
        assert len(hits) == 4
        for h in hits:
            assert not hidden.overlaps(h.chunk.text)
        human = str(answerer.mcq_messages(q, [])[1].content)
        assert f"Answer: {q.answer}" not in human
        assert q.explanation[:60] not in human


def test_page_questions_are_not_heldout_and_hide_the_answer(
    questions: list[Question], heldout: list[Question], sections: list[tuple[str, str, str, str]]
) -> None:
    by_id = {q.id: q for q in questions}
    secs = {sid: (h, t) for _, sid, h, t in sections}
    prompts = load_prompts(PROMPTS, by_id, secs, [q.id for q in heldout])
    held = {q.id for q in heldout}
    pages = [p for p in prompts if p.page is not None and p.page.kind == "question"]
    assert len(pages) >= 4
    for p in pages:
        assert p.page is not None
        assert p.page.ref not in held
        q = by_id[p.page.ref]
        assert "Answer:" not in p.page.text
        assert q.explanation[:80] not in p.page.text
        assert p.page.hidden_answer == q.answer
    with pytest.raises(ValueError, match="held-out"):
        load_prompts(PROMPTS, by_id, secs, [*held, pages[0].page.ref])  # type: ignore[union-attr]
