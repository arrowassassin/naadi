"""Each extractor through the real sandbox (child process, rlimits, no secrets in its environment)."""

from __future__ import annotations

import io
import os

import pytest

from naadi_api.config_api.schemas import IngestRules
from naadi_api.ingest import errors
from naadi_api.ingest.errors import IngestRejected
from naadi_api.ingest.extract import extract_file, extract_text, page_text, render_pages
from naadi_api.ingest.sandbox import _child_env, run_sandboxed

from . import files

RULES = IngestRules()


async def test_pdf_text_headings_figures_and_scanned_pages() -> None:
    doc, out = await extract_file(files.pdf(3, scanned_pages=1), "pdf", "renal.pdf", RULES)
    assert doc.page_count == 4 and doc.title == "Renal physiology notes"
    assert [s.heading for s in doc.sections] == [f"Chapter {i}: Section heading {i}" for i in (1, 2, 3)]
    assert [p.scanned for p in doc.pages] == [False, False, False, True]
    assert len(doc.figures) == 1 and doc.figures[0].page == 2
    assert out["fig-1"][:4] == b"RIFF"  # WebP
    assert "nephron" in doc.full_text()


async def test_pdf_rejections() -> None:
    with pytest.raises(IngestRejected) as e:
        await extract_file(files.encrypted_pdf(), "pdf", "x.pdf", RULES)
    assert e.value.code == errors.ENCRYPTED_PDF
    with pytest.raises(IngestRejected) as e:
        await extract_file(files.pdf(3), "pdf", "x.pdf", RULES.model_copy(update={"max_pages": 2}))
    assert e.value.code == errors.TOO_MANY_PAGES
    with pytest.raises(IngestRejected) as e:
        await extract_file(b"%PDF-1.7\n" + os.urandom(2000), "pdf", "x.pdf", RULES)
    assert e.value.code == errors.UNREADABLE


async def test_pdf_page_render_and_page_text() -> None:
    data = files.pdf(2)
    pngs = await render_pages(data, [2, 9])
    assert list(pngs) == [2] and pngs[2][:8] == b"\x89PNG\r\n\x1a\n"
    text, pages = await page_text(data, 1)
    assert pages == 2 and "Chapter 1" in text


@pytest.mark.parametrize(
    ("data", "kind", "heading", "check"),
    [
        (files.docx(), "docx", "Acid-base disorders", "Metabolic acidosis"),
        (files.pptx(), "pptx", "", "Lecture slide 2"),
        (files.epub(), "epub", "", "Glomerular filtration"),
        (files.html_doc(), "html", "Bicarbonate", "Clinical"),
    ],
)
async def test_markitdown_formats(data: bytes, kind: str, heading: str, check: str) -> None:
    doc, _ = await extract_file(data, kind, f"x.{kind}", RULES)
    assert check in doc.full_text()
    if heading:
        assert doc.sections[0].heading == heading


async def test_xlsx_qa_and_csv_and_text_formats() -> None:
    doc, _ = await extract_file(files.xlsx_qa(), "xlsx", "deck.xlsx", RULES)
    assert len(doc.qa_pairs) == 12 and doc.tables
    doc, _ = await extract_file(files.csv_qa(), "csv", "deck.csv", RULES)
    assert len(doc.qa_pairs) == 10
    doc, _ = await extract_file(files.rtf(), "rtf", "x.rtf", RULES)
    assert doc.sections[0].text.startswith("Renal tubular acidosis")
    doc, _ = await extract_file(files.mcq_text(), "txt", "q.txt", RULES)
    assert len(doc.mcqs) == 3


async def test_images_become_webp_without_metadata() -> None:
    from PIL import Image

    doc, out = await extract_file(files.jpeg_with_exif(), "image", "photo.jpg", RULES)
    webp = Image.open(io.BytesIO(out["image"]))
    assert webp.format == "WEBP" and not webp.getexif()
    assert doc.media.width == 320 and doc.image_key == "image"
    doc, out = await extract_file(files.heic(), "image", "IMG.heic", RULES)
    assert out["image"][:4] == b"RIFF" and doc.media.width == 256


async def test_local_ocr_reads_a_photo() -> None:
    doc, _ = await extract_file(files.png(900, 200, "Loop of Henle concentrates urine"), "image", "x.png", RULES)
    assert "henle" in doc.full_text().lower()


async def test_image_bomb() -> None:
    from PIL import Image

    b = io.BytesIO()
    Image.new("1", (12000, 12000)).save(b, "PNG")  # 144 MP of 1-bit pixels compresses to a few KB
    with pytest.raises(IngestRejected) as e:
        await extract_file(b.getvalue(), "image", "bomb.png", RULES)
    assert e.value.code == errors.IMAGE_TOO_LARGE


async def test_video_is_metadata_and_thumbnail_only() -> None:
    doc, out = await extract_file(files.mp4(), "video", "clip.mp4", RULES)
    assert doc.kind == "video" and doc.media.has_video and doc.media.width == 160
    assert not doc.sections and out["thumb"][:4] == b"RIFF"


async def test_audio_is_probed_and_transcribed_locally() -> None:
    doc, _ = await extract_file(files.wav(1.5), "audio", "memo.wav", RULES)
    assert doc.kind == "audio" and doc.media.has_audio and 1.0 <= (doc.media.duration_s or 0) <= 2.0


async def test_audio_over_the_cap_is_refused_before_transcribing() -> None:
    with pytest.raises(IngestRejected) as e:
        await extract_file(files.wav(61), "audio", "lecture.wav", RULES.model_copy(update={"max_audio_minutes": 1}))
    assert e.value.code == errors.AUDIO_TOO_LONG


async def test_anki_collections() -> None:
    doc, _ = await extract_file(files.apkg(5), "anki", "deck.apkg", RULES)
    assert [q.front for q in doc.qa_pairs][:2] == ["Front 0", "Front 1"] and doc.qa_pairs[0].back == "Back 0\nline"
    with pytest.raises(IngestRejected) as e:
        await extract_file(files.zip_bundle({"collection.anki21b": b"zstd"}), "anki", "x.apkg", RULES)
    assert e.value.code == errors.ANKI_UNSUPPORTED


def test_pasted_text_is_structured_in_process() -> None:
    doc = extract_text("# Title\n\nQ: a?\nA: b\nQ: c?\nA: d", "paste", "My paste")
    assert doc.kind == "paste" and doc.title == "My paste" and len(doc.qa_pairs) == 2


def test_the_sandbox_environment_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-for-children")
    monkeypatch.setenv("NVIDIA_API_KEY", "not-for-children")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    env = _child_env("/tmp/x")
    assert not {"SUPABASE_SERVICE_ROLE_KEY", "NVIDIA_API_KEY", "DATABASE_URL", "OPENROUTER_API_KEY"} & set(env)
    assert env["HOME"] == "/tmp/x" and env["NO_PROXY"] == "*"


async def test_sandbox_limits_kill_a_runaway_parser() -> None:
    with pytest.raises(IngestRejected) as e:
        await run_sandboxed("pdf", files.pdf(1), {}, mem_mb=60)  # far too little memory to even import PyMuPDF
    assert e.value.code in (errors.EXTRACT_FAILED, errors.UNREADABLE, errors.EXTRACT_TIMEOUT)


async def test_unknown_op_is_refused() -> None:
    with pytest.raises(IngestRejected):
        await run_sandboxed("exec", b"print(1)", {})
