"""Step 3, Extract: route a sniffed file to its sandboxed extractor; text inputs (paste, web pages) in-process.

`extract_file` runs every parser of untrusted bytes in the sandbox (`naadi_api.ingest.sandbox`). Pasted text and
text returned by the web-extract layer are plain strings and are structured in-process (pure Python, no parser of a
binary format). The speech model is fetched by the parent (the sandbox has no network) into a local cache.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ...config_api.schemas import IngestRules
from .. import errors
from ..errors import IngestRejected
from ..kinds import MARKITDOWN_KINDS
from ..sandbox import run_sandboxed
from .model import ExtractedDoc
from .workers import op_text

WHISPER_REPO = "Systran/faster-whisper-base.en"
_whisper_lock = asyncio.Lock()


def whisper_dir() -> Path:
    return Path(os.environ.get("NAADI_WHISPER_DIR") or Path.home() / ".cache" / "naadi" / "whisper")


async def ensure_whisper() -> str:
    """The local directory of the base.en int8 model (downloaded once through huggingface_hub)."""
    target = whisper_dir() / "base.en"
    async with _whisper_lock:
        if (target / "model.bin").is_file():
            return str(target)

        def fetch() -> str:
            from huggingface_hub import snapshot_download

            return snapshot_download(WHISPER_REPO, local_dir=str(target))

        return await asyncio.to_thread(fetch)


def _title_hint(name: str | None) -> str:
    if not name:
        return ""
    stem = name.rsplit("/", 1)[-1]
    return (stem.rsplit(".", 1)[0] if "." in stem else stem).replace("_", " ").strip()[:120]


def base_params(rules: IngestRules, name: str | None) -> dict[str, Any]:
    return {
        "max_image_megapixels": rules.max_image_megapixels,
        "max_pages": rules.max_pages,
        "local_ocr_pages": rules.local_ocr_pages,
        "max_audio_minutes": rules.max_audio_minutes,
        "name_title": _title_hint(name),
    }


async def extract_file(
    data: bytes, kind: str, name: str | None, rules: IngestRules
) -> tuple[ExtractedDoc, dict[str, bytes]]:
    params = base_params(rules, name)
    if kind == "pdf":
        # 50 MB PDFs: ~1 s/page with OCR of scanned pages; generous wall clock, CPU limit inside.
        return await run_sandboxed("pdf", data, params, timeout_s=900.0)
    if kind in MARKITDOWN_KINDS:
        return await run_sandboxed("markitdown", data, {**params, "kind": kind}, timeout_s=300.0)
    if kind in ("txt", "md", "csv", "rtf"):
        return await run_sandboxed("text", data, {**params, "kind": kind}, timeout_s=120.0)
    if kind == "image":
        return await run_sandboxed("image", data, params, timeout_s=180.0)
    if kind in ("audio", "video"):
        model = await ensure_whisper() if kind == "audio" else None
        p = {**params, "whisper_model": model or "base.en", "whisper_local_only": True}
        return await run_sandboxed("media", data, p, timeout_s=float(rules.max_audio_minutes * 60 + 120))
    if kind == "anki":
        return await run_sandboxed("anki", data, params, timeout_s=120.0)
    raise IngestRejected(errors.UNSUPPORTED_TYPE)


def extract_text(text: str, kind: str, title: str) -> ExtractedDoc:
    """Pasted text or a web page's extracted text (already a string): structure only."""
    doc, _files = op_text(text.encode("utf-8"), {"kind": "md" if kind == "link" else "paste", "name_title": title})
    return doc.model_copy(update={"kind": kind, "title": title[:300]})


async def render_pages(data: bytes, pages: list[int]) -> dict[int, bytes]:
    _doc, files = await run_sandboxed("render_pages", data, {"pages": pages}, timeout_s=300.0)
    return {int(k.split("-", 1)[1]): v for k, v in files.items() if k.startswith("page-")}


async def page_text(data: bytes, page: int) -> tuple[str, int]:
    doc, _files = await run_sandboxed("page_text", data, {"page": page}, timeout_s=60.0)
    return (doc.pages[0].text if doc.pages else ""), doc.page_count


__all__ = ["ExtractedDoc", "ensure_whisper", "extract_file", "extract_text", "page_text", "render_pages"]
