"""Extractors for untrusted bytes. They run ONLY inside the extraction sandbox (`naadi_api.ingest.sandbox`), a child
process with memory, CPU-time, file-size and open-file limits and an environment without any secret.

Each `op` takes the file bytes and request parameters and returns `(ExtractedDoc, files)`, where `files` are WebP or
PNG images the parent may upload (figures, the converted image, a video thumbnail, rendered pages). Nothing is
executed from a file: PDFs are read with PyMuPDF (no JavaScript), Office files with markitdown (no macros: those
files never get here), images with Pillow (decompression-bomb limit), audio/video with PyAV, Anki collections with
sqlite3 opened read-only from a private temp file.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import tempfile
import warnings
import zipfile
from collections import Counter
from collections.abc import Callable
from html import unescape
from typing import Any

from .. import errors
from ..errors import IngestRejected
from .model import ExtractedDoc, Figure, Media, Page, Section, Table
from .structure import (
    clean_text,
    mcqs_from_text,
    parse_csv,
    qa_from_table,
    qa_from_text,
    sections_from_markdown,
    sections_from_pages,
    short_heading,
    split_by_words,
    strip_rtf,
)

Files = dict[str, bytes]
Result = tuple[ExtractedDoc, Files]

SCANNED_CHARS = 50  # a page with fewer characters of embedded text is an image of a page
FIGURE_MIN_PX = 160
FIGURE_MAX_SIDE = 1800
IMAGE_MAX_SIDE = 2400
MAX_FIGURES = 60
OCR_DPI = 150
RENDER_DPI = 144
WEBP_QUALITY = 82


# ── shared helpers ──


def _pil() -> Any:
    from PIL import Image

    return Image


def _set_pixel_limit(megapixels: int) -> None:
    Image = _pil()
    Image.MAX_IMAGE_PIXELS = megapixels * 1_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)


def _to_webp(img: Any, max_side: int) -> tuple[bytes, int, int]:
    Image = _pil()
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    # A fresh save carries no EXIF / XMP / GPS: metadata of the original never leaves the sandbox.
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue(), img.size[0], img.size[1]


def _open_image(data: bytes, megapixels: int) -> Any:
    Image = _pil()
    _set_pixel_limit(megapixels)
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - pinned dependency
        pass
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > megapixels * 1_000_000:
            raise IngestRejected(errors.IMAGE_TOO_LARGE, limit_mp=megapixels)
        img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise IngestRejected(errors.IMAGE_TOO_LARGE, limit_mp=megapixels) from exc
    except IngestRejected:
        raise
    except Exception as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    from PIL import ImageOps

    return ImageOps.exif_transpose(img)


_OCR: Any = None


def ocr_png(png_or_img: Any) -> str:
    """Local OCR (rapidocr, CPU, bundled models, no network). Lines joined top to bottom."""
    global _OCR
    import logging

    import numpy as np

    if _OCR is None:
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR

        _OCR = RapidOCR(params={"Global.log_level": "error"})
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    img = png_or_img
    if isinstance(img, bytes | bytearray):
        img = _pil().open(io.BytesIO(img))
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, 2000 / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    res = _OCR(np.array(img))
    txts = getattr(res, "txts", None) or ()
    boxes = getattr(res, "boxes", None)
    if boxes is not None and len(txts) == len(boxes):
        order = sorted(range(len(txts)), key=lambda i: (round(float(boxes[i][0][1]) / 12), float(boxes[i][0][0])))
        txts = tuple(txts[i] for i in order)
    return clean_text("\n".join(str(t) for t in txts))


# ── PDF ──


def _pymupdf() -> Any:
    import importlib

    return importlib.import_module("pymupdf")


def _pdf_open(data: bytes) -> Any:
    pymupdf = _pymupdf()

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    if doc.needs_pass or doc.is_encrypted:
        raise IngestRejected(errors.ENCRYPTED_PDF)
    return doc


def _pdf_headings(doc: Any, max_pages: int) -> list[tuple[int, str, int]]:
    """Outline entries, else lines set clearly larger than body text (level, heading, page)."""
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # noqa: BLE001
        toc = []
    if len(toc) >= 2:
        return [(int(lv), str(t), int(p)) for lv, t, p in toc[:2000] if isinstance(p, int) and p >= 1]
    sizes: Counter[float] = Counter()
    lines: list[tuple[float, str, int, bool]] = []
    for pno in range(min(doc.page_count, max_pages)):
        page = doc.load_page(pno)
        d = page.get_text("dict", flags=0)
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                size = round(max(float(s.get("size", 0)) for s in spans), 1)
                bold = any(int(s.get("flags", 0)) & 16 for s in spans)
                sizes[size] += len(text)
                lines.append((size, text, pno + 1, bold))
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    out: list[tuple[int, str, int]] = []
    for size, text, pno, bold in lines:
        if (
            3 <= len(text) <= 120
            and not text.endswith((".", ",", ";"))
            and (size >= body * 1.3 or (bold and size >= body * 1.15))
        ):
            level = 1 if size >= body * 1.6 else 2
            if not out or out[-1][1] != text:
                out.append((level, text, pno))
    # Too many "headings" means the heuristic failed (e.g. slides with large text): use page ranges instead.
    return out if 2 <= len(out) <= max(8, doc.page_count * 2) else []


def op_pdf(data: bytes, p: dict[str, Any]) -> Result:
    pymupdf = _pymupdf()

    _set_pixel_limit(int(p.get("max_image_megapixels", 50)))
    doc = _pdf_open(data)
    max_pages = int(p.get("max_pages", 2000))
    if doc.page_count > max_pages:
        raise IngestRejected(errors.TOO_MANY_PAGES, limit=max_pages)
    ocr_budget = int(p.get("local_ocr_pages", 200))
    want_figures = bool(p.get("figures", True))
    files: Files = {}
    figures: list[Figure] = []
    pages: list[Page] = []
    seen_xrefs: set[int] = set()
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        text = clean_text(page.get_text("text", sort=True) or "")
        scanned = len(text) < SCANNED_CHARS
        source = "text"
        if scanned:
            source = "none"
            if ocr_budget > 0:
                pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY, alpha=False)
                ocr = ocr_png(pix.tobytes("png"))
                ocr_budget -= 1
                if ocr:
                    text, source = ocr, "local_ocr"
        pages.append(Page(number=pno + 1, text=text[:400_000], source=source, scanned=scanned))
        if want_figures and len(figures) < MAX_FIGURES and not scanned:
            for info in page.get_images(full=True):
                xref = int(info[0])
                if xref in seen_xrefs or len(figures) >= MAX_FIGURES:
                    continue
                seen_xrefs.add(xref)
                try:
                    w, h = int(info[2]), int(info[3])
                    if w < FIGURE_MIN_PX or h < FIGURE_MIN_PX or w * h > 25_000_000:
                        continue
                    raw = doc.extract_image(xref)
                    img = _pil().open(io.BytesIO(raw["image"]))
                    img.load()
                    webp, ww, hh = _to_webp(img, FIGURE_MAX_SIDE)
                except Exception:  # noqa: BLE001, S112 - one broken embedded image never fails the document
                    continue
                key = f"fig-{len(figures) + 1}"
                files[key] = webp
                figures.append(Figure(key=key, page=pno + 1, width=ww, height=hh, bytes=len(webp)))
    title = clean_text(str((doc.metadata or {}).get("title") or ""))[:300]
    sections = sections_from_pages(pages, title or str(p.get("name_title", "")), _pdf_headings(doc, max_pages))
    full = "\n".join(pg.text for pg in pages)
    out = ExtractedDoc(
        kind="pdf",
        title=short_heading(title, 300) if title else "",
        pages=pages,
        sections=sections,
        figures=figures,
        page_count=doc.page_count,
        mcqs=mcqs_from_text(full),
        qa_pairs=qa_from_text(full),
    )
    return out, files


def op_render_pages(data: bytes, p: dict[str, Any]) -> Result:
    """PNG renders of the requested pages (for the vision role), at most 2000 px on the long side."""
    pymupdf = _pymupdf()

    doc = _pdf_open(data)
    files: Files = {}
    for n in [int(x) for x in p.get("pages", [])][:500]:
        if not 1 <= n <= doc.page_count:
            continue
        page = doc.load_page(n - 1)
        rect = page.rect
        dpi = min(RENDER_DPI, int(2000 / max(rect.width, rect.height) * 72))
        pix = page.get_pixmap(dpi=max(dpi, 50), colorspace=pymupdf.csRGB, alpha=False)
        files[f"page-{n}"] = pix.tobytes("png")
    return ExtractedDoc(kind="pdf", page_count=doc.page_count), files


def op_page_text(data: bytes, p: dict[str, Any]) -> Result:
    doc = _pdf_open(data)
    n = int(p.get("page", 1))
    if not 1 <= n <= doc.page_count:
        return ExtractedDoc(kind="pdf", page_count=doc.page_count), {}
    text = clean_text(doc.load_page(n - 1).get_text("text", sort=True) or "")
    return ExtractedDoc(kind="pdf", page_count=doc.page_count, pages=[Page(number=n, text=text[:400_000])]), {}


# ── Office / EPUB / HTML (markitdown) ──
_MEDIA_PREFIX = {"docx": "word/media/", "pptx": "ppt/media/", "xlsx": "xl/media/", "epub": ""}
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")


def _office_figures(data: bytes, kind: str, files: Files) -> list[Figure]:
    prefix = _MEDIA_PREFIX.get(kind)
    if prefix is None:
        return []
    out: list[Figure] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return []
    for info in zf.infolist():
        if len(out) >= MAX_FIGURES:
            break
        name = info.filename.lower()
        if not name.startswith(prefix) or not name.endswith(_IMG_EXT) or info.file_size > 25 * 1024 * 1024:
            continue
        try:
            with zf.open(info) as fh:
                raw = fh.read(info.file_size + 1)
            if len(raw) > info.file_size:
                continue
            img = _pil().open(io.BytesIO(raw))
            if img.width < FIGURE_MIN_PX or img.height < FIGURE_MIN_PX:
                continue
            img.load()
            webp, w, h = _to_webp(img, FIGURE_MAX_SIDE)
        except Exception:  # noqa: BLE001, S112 - one broken embedded image never fails the document
            continue
        key = f"fig-{len(out) + 1}"
        files[key] = webp
        out.append(Figure(key=key, width=w, height=h, bytes=len(webp)))
    return out


def _md_tables(md: str) -> list[Table]:
    tables: list[Table] = []
    cur: list[list[str]] = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        elif cur:
            if len(cur) >= 2:
                tables.append(Table(rows=cur[:5000]))
            cur = []
    if len(cur) >= 2:
        tables.append(Table(rows=cur[:5000]))
    return tables[:200]


def op_markitdown(data: bytes, p: dict[str, Any]) -> Result:
    from markitdown import MarkItDown, StreamInfo

    _set_pixel_limit(int(p.get("max_image_megapixels", 50)))
    kind = str(p["kind"])
    ext = {"docx": ".docx", "pptx": ".pptx", "xlsx": ".xlsx", "epub": ".epub", "html": ".html"}[kind]
    try:
        md = MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=ext))
        text = str(md.markdown or md.text_content or "")
        title = str(getattr(md, "title", "") or "")
    except Exception as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    files: Files = {}
    figures = _office_figures(data, kind, files)
    tables = _md_tables(text)
    title = clean_text(title)[:300]
    sections = sections_from_markdown(text, title or str(p.get("name_title", "")))
    plain = "\n".join(s.text for s in sections)
    qa = qa_from_text(plain)
    if not qa and kind == "xlsx" and tables:
        qa = qa_from_table(tables[0])
    out = ExtractedDoc(
        kind=kind,
        title=title,
        sections=sections,
        figures=figures,
        tables=tables,
        qa_pairs=qa,
        mcqs=mcqs_from_text(plain),
        page_count=len([ln for ln in text.splitlines() if ln.startswith("<!-- Slide number")]) if kind == "pptx" else 0,
    )
    return out, files


# ── plain text, Markdown, CSV, RTF ──


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def op_text(data: bytes, p: dict[str, Any]) -> Result:
    kind = str(p["kind"])
    raw = _decode(data)
    title = str(p.get("name_title", ""))
    tables: list[Table] = []
    qa = []
    if kind == "csv":
        table = parse_csv(raw)
        tables = [table]
        qa = qa_from_table(table)
        text = "\n".join(" | ".join(r) for r in table.rows)
        sections = split_by_words(text, title) if text else []
    else:
        text = strip_rtf(raw) if kind == "rtf" else clean_text(raw)
        sections = sections_from_markdown(text, title) if kind in ("md", "txt", "paste") else []
        if not sections and text:
            sections = split_by_words(text, title)
        qa = qa_from_text(text)
    plain = "\n".join(s.text for s in sections)
    return ExtractedDoc(kind=kind, sections=sections, tables=tables, qa_pairs=qa, mcqs=mcqs_from_text(plain)), {}


# ── images ──


def op_image(data: bytes, p: dict[str, Any]) -> Result:
    img = _open_image(data, int(p.get("max_image_megapixels", 50)))
    webp, w, h = _to_webp(img, IMAGE_MAX_SIDE)
    text = ocr_png(img) if p.get("ocr", True) else ""
    files: Files = {"image": webp}
    words = len(text.split())
    # Photos of handwriting OCR poorly: few confident words on a large image is the hint (the vision role decides).
    handwriting = words < 40 and w * h > 600_000
    doc = ExtractedDoc(
        kind="image",
        pages=[Page(number=1, text=text, source="local_ocr", scanned=True)],
        sections=[Section(heading=str(p.get("name_title", ""))[:300], text=text)] if text else [],
        figures=[Figure(key="image", width=w, height=h, bytes=len(webp))],
        media=Media(width=w, height=h),
        image_key="image",
        handwriting_likely=handwriting,
        page_count=1,
    )
    return doc, files


def op_ocr(data: bytes, p: dict[str, Any]) -> Result:
    img = _open_image(data, int(p.get("max_image_megapixels", 50)))
    return ExtractedDoc(kind="image", pages=[Page(number=1, text=ocr_png(img), source="local_ocr")]), {}


# ── audio / video (PyAV, faster-whisper) ──


def _probe(path: str) -> tuple[float, bool, bool, int | None, int | None]:
    import av

    try:
        with av.open(path) as c:
            duration = float(c.duration / 1_000_000) if c.duration else 0.0
            vs = [
                s for s in c.streams if s.type == "video" and not (s.disposition & av.stream.Disposition.attached_pic)
            ]
            aus = [s for s in c.streams if s.type == "audio"]
            w = int(getattr(vs[0].codec_context, "width", 0)) or None if vs else None
            h = int(getattr(vs[0].codec_context, "height", 0)) or None if vs else None
            if not duration:
                for s in vs or aus:
                    if s.duration and s.time_base:
                        duration = float(s.duration * s.time_base)
            return duration, bool(aus), bool(vs), w, h
    except Exception as exc:
        raise IngestRejected(errors.UNREADABLE) from exc


def _tmpfile(data: bytes) -> str:
    fd, path = tempfile.mkstemp(dir=os.environ.get("TMPDIR"))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def _transcribe(path: str, p: dict[str, Any]) -> list[Section]:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    model = WhisperModel(
        str(p.get("whisper_model", "base.en")),
        device="cpu",
        compute_type="int8",
        cpu_threads=int(p.get("threads", 2)),
        download_root=p.get("whisper_dir") or None,
        local_files_only=bool(p.get("whisper_local_only", False)),
    )
    segments, _info = model.transcribe(path, language="en", vad_filter=True, beam_size=1)
    sections: list[Section] = []
    buf: list[str] = []
    start: float | None = None
    window = 300.0  # one section per ~5 minutes of speech
    for seg in segments:
        if start is None:
            start = float(seg.start)
        buf.append(str(seg.text).strip())
        if float(seg.end) - start >= window:
            sections.append(Section(heading=_stamp(start), text=clean_text(" ".join(buf)), start_s=start))
            buf, start = [], None
    if buf and start is not None:
        sections.append(Section(heading=_stamp(start), text=clean_text(" ".join(buf)), start_s=start))
    return sections


def _stamp(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:d}:{s % 60:02d}"


def _thumbnail(path: str, duration: float) -> bytes | None:
    import av

    try:
        with av.open(path) as c:
            stream = next(s for s in c.streams if s.type == "video")
            if duration > 4 and stream.time_base:
                c.seek(int(duration * 0.1 / float(stream.time_base)), stream=stream, any_frame=False)
            for frame in c.decode(stream):
                img = frame.to_image()  # type: ignore[union-attr]
                webp, _w, _h = _to_webp(img, 960)
                return webp
    except Exception:  # noqa: BLE001
        return None
    return None


def op_media(data: bytes, p: dict[str, Any]) -> Result:
    path = _tmpfile(data)
    try:
        duration, has_audio, has_video, w, h = _probe(path)
        files: Files = {}
        media = Media(duration_s=round(duration, 1), has_audio=has_audio, has_video=has_video, width=w, height=h)
        if has_video:
            thumb = _thumbnail(path, duration)
            if thumb:
                files["thumb"] = thumb
                media.thumbnail = "thumb"
            # Own video files: thumbnail + metadata only (no transcript, nothing stored beyond the thumbnail).
            return ExtractedDoc(kind="video", media=media), files
        if not has_audio:
            raise IngestRejected(errors.UNREADABLE)
        if duration > float(p.get("max_audio_minutes", 120)) * 60:
            raise IngestRejected(errors.AUDIO_TOO_LONG, limit_min=int(p.get("max_audio_minutes", 120)))
        sections = _transcribe(path, p)
        return ExtractedDoc(kind="audio", sections=sections, media=media), files
    finally:
        os.unlink(path)


# ── Anki (.apkg / .colpkg) ──
_TAGS = re.compile(r"<[^>]+>")


def _html_text(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</div>|</p>", "\n", s)
    s = re.sub(r"\[sound:[^\]]*\]", "", s)
    return clean_text(unescape(_TAGS.sub("", s)))


def op_anki(data: bytes, p: dict[str, Any]) -> Result:
    from .model import QA

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = {n.lower(): n for n in zf.namelist()}
    except zipfile.BadZipFile as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    member = names.get("collection.anki21") or names.get("collection.anki2")
    if member is None:
        raise IngestRejected(errors.ANKI_UNSUPPORTED)
    info = zf.getinfo(member)
    if info.file_size > 200 * 1024 * 1024:
        raise IngestRejected(errors.FILE_TOO_LARGE)
    path = _tmpfile(zf.read(info))
    pairs: list[QA] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("pragma trusted_schema = off")
            rows = con.execute("select flds from notes limit 5001").fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise IngestRejected(errors.ANKI_UNSUPPORTED) from exc
    finally:
        os.unlink(path)
    for (flds,) in rows:
        parts = [_html_text(x) for x in str(flds).split("\x1f")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            pairs.append(QA(front=parts[0][:4000], back=parts[1][:8000]))
    # Newer exports (.anki21b only) carry a one-note legacy collection asking to update Anki.
    if names.get("collection.anki21b") and len(pairs) <= 1:
        raise IngestRejected(errors.ANKI_UNSUPPORTED)
    if not pairs:
        raise IngestRejected(errors.NO_TEXT)
    return ExtractedDoc(kind="anki", qa_pairs=pairs[:5000]), {}


OPS: dict[str, Callable[[bytes, dict[str, Any]], Result]] = {
    "pdf": op_pdf,
    "render_pages": op_render_pages,
    "page_text": op_page_text,
    "markitdown": op_markitdown,
    "text": op_text,
    "image": op_image,
    "ocr": op_ocr,
    "media": op_media,
    "anki": op_anki,
}
