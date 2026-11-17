"""Unit tests for universal ingest (no database, no network): sniffing, guards, the sensitivity gate, structure,
placements (registry, proposals, grouping), Understand / vision reply parsing."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from naadi_api.config_api.schemas import IngestRules
from naadi_api.ingest import errors, sensitivity
from naadi_api.ingest.errors import IngestRejected
from naadi_api.ingest.extract.model import MCQ, QA, ExtractedDoc, Figure, Page, Section
from naadi_api.ingest.extract.structure import (
    mcqs_from_text,
    parse_csv,
    qa_from_table,
    qa_from_text,
    sections_from_markdown,
    sections_from_pages,
    strip_rtf,
)
from naadi_api.ingest.placements import (
    Candidate,
    IngestView,
    PlacementRegistryError,
    accepted_inputs,
    propose_all,
    register_placement,
    registered,
)
from naadi_api.ingest.placements import content as c
from naadi_api.ingest.placements.registry import unregister_placement
from naadi_api.ingest.secure import check_size, inspect_container, list_bundle, read_member
from naadi_api.ingest.sniff import sniff
from naadi_api.ingest.understand import Understanding, parse_reply, rules_understanding, understand
from naadi_api.ingest.vision import UnreadableReply
from naadi_api.ingest.vision import parse_reply as parse_vision
from naadi_api.llm_budget import BudgetExhausted

from . import files

RULES = IngestRules()


# ── sniffing: bytes decide, names lie ──


@pytest.mark.parametrize(
    ("data", "name", "kind"),
    [
        (files.pdf(1), "notes.exe", "pdf"),
        (files.docx(), "lecture.pdf", "docx"),
        (files.pptx(), "x.bin", "pptx"),
        (files.xlsx_qa(), "deck.csv", "xlsx"),
        (files.epub(), "book.zip", "epub"),
        (files.apkg(), "deck.zip", "anki"),
        (files.zip_bundle({"a.txt": b"hi"}), "bundle.pdf", "zip"),
        (files.png(), "photo.pdf", "image"),
        (files.jpeg_with_exif(), "x", "image"),
        (files.heic(), "IMG_0001.jpg", "image"),
        (files.wav(), "x.mp3", "audio"),
        (files.mp4(), "clip.mov", "video"),
        (files.html_doc(), "page.txt", "html"),
        (files.rtf(), "doc.txt", "rtf"),
        (files.csv_qa(), "deck.csv", "csv"),
        (b"# Heading\n\nSome notes", "n.md", "md"),
        (b"Plain notes about the kidney.", "n.txt", "txt"),
    ],
)
def test_sniff_by_content(data: bytes, name: str, kind: str) -> None:
    assert sniff(data, name).kind == kind


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"MZ\x90\x00" + b"\x00" * 200, errors.EXECUTABLE),
        (b"\x7fELF\x02\x01\x01" + b"\x00" * 200, errors.EXECUTABLE),
        (b"\xcf\xfa\xed\xfe" + b"\x00" * 200, errors.EXECUTABLE),
        (b"#!/bin/sh\nrm -rf /\n", errors.EXECUTABLE),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500, errors.LEGACY_OFFICE),
        (files.docx(macro=True), errors.MACRO_OFFICE),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', errors.SVG_UNSUPPORTED),
        (b"", errors.EMPTY_FILE),
        (bytes(range(256)) * 40, errors.UNSUPPORTED_TYPE),
    ],
)
def test_sniff_rejects(data: bytes, code: str) -> None:
    with pytest.raises(IngestRejected) as e:
        sniff(data, "lecture.pdf")
    assert e.value.code == code


def test_a_jar_is_an_executable() -> None:
    jar = files.zip_bundle({"META-INF/MANIFEST.MF": b"Main-Class: X", "X.class": b"\xca\xfe\xba\xbe"})
    with pytest.raises(IngestRejected) as e:
        sniff(jar, "notes.zip")
    assert e.value.code == errors.EXECUTABLE


# ── caps and archive guards ──


def test_size_caps() -> None:
    check_size(1, RULES)
    with pytest.raises(IngestRejected) as e:
        check_size(RULES.max_file_mb * 1024 * 1024 + 1, RULES)
    assert e.value.code == errors.FILE_TOO_LARGE
    with pytest.raises(IngestRejected):
        check_size(0, RULES)


def test_zip_bomb_ratio() -> None:
    with pytest.raises(IngestRejected) as e:
        list_bundle(files.zip_bomb(), RULES)
    assert e.value.code == errors.ZIP_BOMB


def test_zip_entry_and_size_caps() -> None:
    many = files.zip_bundle({f"f{i}.txt": b"x" for i in range(30)})
    with pytest.raises(IngestRejected) as e:
        list_bundle(many, RULES.model_copy(update={"zip_max_entries": 10}))
    assert e.value.code == errors.ZIP_TOO_MANY
    import os

    big = files.zip_bundle({"a.bin": os.urandom(2 * 1024 * 1024)})
    with pytest.raises(IngestRejected) as e:
        list_bundle(big, RULES.model_copy(update={"zip_max_uncompressed_mb": 1}))
    assert e.value.code == errors.ZIP_TOO_LARGE


def test_zip_encrypted_and_unsafe_paths() -> None:
    with pytest.raises(IngestRejected) as e:
        list_bundle(files.encrypted_zip_flag(), RULES)
    assert e.value.code == errors.ENCRYPTED_ARCHIVE
    for bad in ("../evil.txt", "/abs.txt", "a/../../b.txt"):
        with pytest.raises(IngestRejected) as e:
            list_bundle(files.zip_bundle({bad: b"x"}), RULES)
        assert e.value.code == errors.ZIP_UNSAFE_PATH


def test_zip_skips_os_metadata_and_reads_members_bounded() -> None:
    z = files.zip_bundle({"__MACOSX/._a.pdf": b"junk", "dir/a.txt": b"hello", ".DS_Store": b"x"})
    members = list_bundle(z, RULES)
    assert [m.name for m in members] == ["dir/a.txt"]
    assert read_member(z, members[0], 1024) == b"hello"
    with pytest.raises(IngestRejected):
        read_member(z, members[0], 2)  # more bytes than the cap


def test_container_guard_catches_bombs_inside_office_files() -> None:
    inspect_container(files.docx(), max_ratio=100)
    bomb = files.zip_bundle({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"a" * (5 * 1024 * 1024)})
    with pytest.raises(IngestRejected) as e:
        inspect_container(bomb, max_ratio=100)
    assert e.value.code == errors.ZIP_BOMB


# ── sensitivity gate (synthetic identifiers only) ──


def test_verhoeff_and_aadhaar() -> None:
    n = files.aadhaar()
    assert sensitivity.verhoeff_valid(n)
    bad = n[:-1] + str((int(n[-1]) + 1) % 10)
    assert not sensitivity.verhoeff_valid(bad)
    hit = sensitivity.scan(f"UIDAI card {n[:4]} {n[4:8]} {n[8:]}")
    assert "aadhaar" in hit.signals and hit.doc_type == "aadhaar"
    assert not sensitivity.scan(f"Reference {bad[:4]} {bad[4:8]} {bad[8:]}").personal


def test_passport_mrz() -> None:
    hit = sensitivity.scan(files.passport_text())
    assert "passport_mrz" in hit.signals and hit.doc_type == "passport"
    l1, l2 = files.mrz_td3()
    broken = l2[:9] + str((int(l2[9]) + 1) % 10) + l2[10:]
    assert "passport_mrz" not in sensitivity.scan(f"{l1}\n{broken}").signals


def test_pan_ssn_sevis_need_their_labels() -> None:
    assert "pan" in sensitivity.scan("Income Tax Department\nPermanent Account Number ABCPE1234F").signals
    assert "pan" not in sensitivity.scan("Gene ABCPE1234F was expressed").signals
    assert "ssn" in sensitivity.scan("Social Security Number: 123-45-6789").signals
    assert not sensitivity.scan("Lab id 123-45-6789 for the sodium panel").personal
    assert not sensitivity.scan("Social Security Number: 666-45-6789").personal  # invalid area
    assert "sevis" in sensitivity.scan("Form I-20 SEVIS ID N0012345678").signals


def test_document_keywords_need_a_personal_label() -> None:
    assert "doc_diploma" in sensitivity.scan("This is to certify that\nName of the candidate: TEST").signals
    assert "doc_receipt" in sensitivity.scan("Fee receipt\nReceived from: TEST PERSON\nAmount paid 900").signals
    clean = "Lecture: renal tubular acidosis. The diploma exam covers the visa annotation of nephrons. Receipt."
    assert not sensitivity.scan(clean).personal
    assert not sensitivity.scan(files.LOREM * 20).personal


def test_redaction_is_the_second_rules_pass() -> None:
    n = files.aadhaar()
    hit = sensitivity.scan(f"Aadhaar {n}")
    assert sensitivity.redact(f"note {n} end", hit.redactions) == "note [id] end"


# ── structure ──


def test_markdown_sections_and_split() -> None:
    secs = sections_from_markdown("# A\nalpha\n## B\nbeta\n#### deep\nstill b", "T")
    assert [s.heading for s in secs] == ["A", "B"]
    assert "deep" in secs[1].text
    long = sections_from_markdown(("word " * 500 + "\n\n") * 8, "Long")
    assert len(long) >= 2 and long[0].heading.startswith("Long")


def test_sections_from_pages_outline_or_ranges() -> None:
    pages = [Page(number=i, text=f"page {i} " * 20) for i in range(1, 21)]
    by_outline = sections_from_pages(pages, "T", [(1, "Intro", 1), (1, "Kidney", 6), (2, "Tubule", 12)])
    assert [(s.heading, s.page_start, s.page_end) for s in by_outline] == [
        ("Intro", 1, 5),
        ("Kidney", 6, 11),
        ("Tubule", 12, 20),
    ]
    by_range = sections_from_pages(pages, "T", [])
    assert [(s.page_start, s.page_end) for s in by_range] == [(1, 8), (9, 16), (17, 20)]


def test_qa_and_mcq_parsing() -> None:
    table = parse_csv(files.csv_qa().decode())
    assert len(qa_from_table(table)) == 10
    assert len(qa_from_text("Q: one?\nA: yes\nQ: two?\nA: no")) == 2
    mcqs = mcqs_from_text(files.mcq_text().decode())
    assert len(mcqs) == 3 and mcqs[0].answer == "A" and len(mcqs[0].options) == 4
    assert "two thirds" in mcqs[0].explanation
    assert mcqs_from_text("1. Stem without options\nAnswer: B") == []


def test_rtf_stripping() -> None:
    assert strip_rtf(files.rtf().decode()).startswith("Renal tubular acidosis")
    assert strip_rtf("{\\rtf1 caf\\'e9 \\u8364? x}") == "caf\u00e9 \u20ac x"


def test_content_blocks_escape_and_shape() -> None:
    blocks = c.blocks_from_text("## Heading\n- one\n- two\n\n<script>x</script>\n| a | b |\n|---|---|\n| 1 | 2 |")
    types = [b["type"] for b in blocks]
    assert types == ["heading", "bulletList", "paragraph", "table"]
    assert blocks[2]["content"][0]["text"] == "<script>x</script>"  # a text node, never markup
    assert "<script>" not in c.html_from_text("<script>x</script>")
    item = uuid.uuid4()
    assert c.chapter_id(item).startswith("u-") and c.section_id(item, 2).endswith("-s2")


# ── placements ──


def _view(doc: ExtractedDoc, kind: str = "reading", input_kind: str = "pdf", **kw: Any) -> IngestView:
    return IngestView(
        item_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        input_kind=input_kind,
        understanding=Understanding(kind=kind, title="Renal notes"),  # type: ignore[arg-type]
        doc=doc,
        original_name="renal.pdf",
        flags={"library": True},
        **kw,
    )


def test_reading_material_recommends_a_private_chapter() -> None:
    doc = ExtractedDoc(kind="pdf", sections=[Section(heading="A", text=files.LOREM * 20)], page_count=3)
    ranked = propose_all(_view(doc))
    assert ranked[0][1].kind == "private_chapter"
    assert ranked[0][1].preview["sections"] == 1 and ranked[0][1].preview["title"] == "Renal notes"
    assert {r.kind for r, _ in ranked} >= {"private_chapter", "kb_entry"}


def test_qa_and_mcq_items_offer_cards_and_questions_without_answers() -> None:
    doc = ExtractedDoc(
        kind="csv",
        qa_pairs=[QA(front=f"f{i}", back=f"b{i}") for i in range(5)],
        mcqs=[MCQ(stem=f"s{i}", options=["x", "y"], answer="B") for i in range(3)],
    )
    ranked = propose_all(_view(doc, kind="question_set", input_kind="csv"))
    kinds = [c.kind for _, c in ranked]
    assert kinds[0] == "private_questions" and "flashcards" in kinds
    for _, cand in ranked:
        assert "answer" not in str(cand.preview).lower()
        assert "B" not in cand.preview.values()


def test_images_offer_notebook_page_and_figure_overlays() -> None:
    fig = Figure(key="image", width=800, height=600, storage_path="user-uploads/u/i/image.webp", caption="Nephron")
    doc = ExtractedDoc(
        kind="image", figures=[fig], image_key="image", sections=[Section(heading="x", text="loop of henle " * 10)]
    )
    matches = ({"section_id": "ch05-s2", "chapter_id": "ch05", "chapter_title": "Renal", "heading": "Tubule"},)
    ranked = propose_all(_view(doc, kind="handwritten_notes", input_kind="image", matches=matches))
    assert ranked[0][1].kind == "notebook_page"
    assert any(c.kind == "figure_overlay" and c.params["section_id"] == "ch05-s2" for _, c in ranked)
    ranked = propose_all(_view(doc, kind="figure", input_kind="image", matches=matches))
    assert ranked[0][1].kind == "figure_overlay"


class _Planner:
    kind = "planner_items_test"
    inputs = frozenset({"ics_test"})

    def propose(self, view: IngestView) -> list[Candidate]:
        return [Candidate(self.kind, 0.99, {"rows": 3})] if view.understanding.kind == "schedule" else []

    async def apply(self, ctx: Any, cand: Candidate) -> list[Any]:
        return []

    async def undo(self, ctx: Any, records: list[Any]) -> None:
        return None


def test_registry_contract_flags_and_accepts() -> None:
    register_placement("planner_items_test", _Planner(), requires_flag="planner")
    try:
        with pytest.raises(PlacementRegistryError):
            register_placement("planner_items_test", _Planner())
        with pytest.raises(PlacementRegistryError):
            register_placement("Bad-Kind", _Planner())
        doc = ExtractedDoc(kind="xlsx", sections=[Section(heading="Plan", text=files.LOREM * 5)])
        off = _view(doc, kind="schedule", input_kind="xlsx")
        assert all(r.kind != "planner_items_test" for r, _ in propose_all(off))
        on = IngestView(**{**off.__dict__, "flags": {"library": True, "planner": True}})
        assert propose_all(on)[0][0].kind == "planner_items_test"
        assert "ics_test" in accepted_inputs({"planner": True}) and "ics_test" not in accepted_inputs({})
        assert [r.kind for r in registered()][:6] == [
            "private_chapter",
            "kb_entry",
            "figure_overlay",
            "notebook_page",
            "flashcards",
            "private_questions",
        ]
    finally:
        unregister_placement("planner_items_test")


def test_a_broken_handler_never_blocks_the_item() -> None:
    class Broken(_Planner):
        kind = "broken_test"

        def propose(self, view: IngestView) -> list[Candidate]:
            raise RuntimeError("boom")

    register_placement("broken_test", Broken())
    try:
        doc = ExtractedDoc(kind="pdf", sections=[Section(heading="A", text=files.LOREM * 20)])
        assert propose_all(_view(doc))[0][1].kind == "private_chapter"
    finally:
        unregister_placement("broken_test")


# ── understand / vision replies ──

TAX = {"step1.sys.respiratory_renal": "Respiratory & Renal", "step1.disc.physiology": "Physiology"}


def test_understanding_parse_validates_taxonomy_and_kind() -> None:
    u = parse_reply(
        '```json\n{"kind": "reading", "title": "Renal  notes", "taxonomy_ids": ["step1.sys.respiratory_renal",'
        ' "made.up"], "language": "EN", "quality": "good"}\n```',
        TAX,
    )
    assert (u.kind, u.title, u.taxonomy_ids, u.language, u.source) == (
        "reading",
        "Renal notes",
        ["step1.sys.respiratory_renal"],
        "en",
        "model",
    )
    with pytest.raises(ValueError, match="no JSON"):
        parse_reply("no json here", TAX)


def test_rules_understanding() -> None:
    qa = ExtractedDoc(kind="csv", qa_pairs=[QA(front="a", back="b")] * 10)
    assert rules_understanding(qa, "csv", "deck.csv").kind == "flashcard_deck"
    long = ExtractedDoc(kind="pdf", sections=[Section(text=files.LOREM * 100)], page_count=12)
    assert rules_understanding(long, "pdf", "My_Renal-Notes.pdf").title == "My Renal Notes"


class _Gateway:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, Any]] = []

    async def ainvoke(self, role: str, messages: Any, ctx: Any, **kw: Any) -> Any:
        self.calls.append((role, messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return AIMessage(content=r)


async def test_understand_retries_once_then_falls_back() -> None:
    doc = ExtractedDoc(kind="pdf", sections=[Section(heading="A", text=files.LOREM * 30)], page_count=6)
    gw = _Gateway(["garbage", '{"kind": "lecture_notes", "title": "Renal"}'])
    u = await understand(
        gw, doc, input_kind="pdf", name_hint="x.pdf", taxonomy=TAX, user_id=uuid.uuid4(), item_id=uuid.uuid4()
    )
    assert u.kind == "lecture_notes" and len(gw.calls) == 2 and gw.calls[0][0] == "structured"
    gw = _Gateway([BudgetExhausted("daily_cap", __import__("datetime").datetime.now())])
    u = await understand(
        gw, doc, input_kind="pdf", name_hint="x.pdf", taxonomy=TAX, user_id=uuid.uuid4(), item_id=uuid.uuid4()
    )
    assert u.source == "rules" and u.kind == "reading"


async def test_understand_prompt_is_bounded_and_has_no_file_bytes() -> None:
    doc = ExtractedDoc(kind="pdf", sections=[Section(heading="A", text=files.LOREM * 2000)], page_count=60)
    gw = _Gateway(['{"kind": "reading", "title": "x"}'])
    await understand(
        gw, doc, input_kind="pdf", name_hint="x.pdf", taxonomy=TAX, user_id=uuid.uuid4(), item_id=uuid.uuid4()
    )
    human = gw.calls[0][1][1].content
    assert isinstance(human, str) and len(human) <= 4000


def test_vision_reply_parsing() -> None:
    v = parse_vision('{"text": "Loop of Henle\\u0007", "handwritten": true, "caption": "A nephron"}')
    assert v.text == "Loop of Henle" and v.handwritten and v.caption == "A nephron"
    with pytest.raises(UnreadableReply):
        parse_vision("I cannot read this")


# ── answer columns: ingest writes a student's own MCQs, it never reads answers back ──


def test_ingest_only_ever_inserts_the_answer_columns() -> None:
    """tests/qbank/test_qbank_db.py allowlists ingest/placements/builtin.py for the secret question columns. That
    entry holds only while every mention is the private-question INSERT (no select, no returning) and no other
    ingest module mentions them: answers reach a client only through the Qbank API, after an attempt."""
    import re
    from pathlib import Path

    secret = re.compile(r"\b(answer_html|explanation_html|why_not_html|educational_objective)\b")
    root = Path(__file__).resolve().parents[2] / "naadi_api" / "ingest"
    mentions: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        # Each SQL statement as written: adjacent string literals joined.
        for stmt in re.findall(r'"((?:insert|select|update|delete|with)\b[^"]*"(?:\s*"[^"]*")*)', src, re.I):
            sql = re.sub(r'"\s*"', "", stmt)
            if secret.search(sql):
                mentions.setdefault(path.relative_to(root).as_posix(), []).append(sql)
        # Any other mention (an f-string, a dict key, a model field) would be a read path: none allowed.
        outside = secret.sub("", src) != src and path.name != "builtin.py"
        assert not outside, f"{path.name} mentions an answer column"
    assert list(mentions) == ["placements/builtin.py"]
    for sql in mentions["placements/builtin.py"]:
        low = sql.lower()
        assert low.startswith("insert into public.questions"), sql
        assert "returning" not in low and "select" not in low, sql
