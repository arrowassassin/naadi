"""What a file really is, from its bytes (never its name or the declared content type).

The name is used only to choose between text flavours that bytes cannot tell apart (`.csv` vs `.tsv` vs `.md` vs
plain text). A PDF named `notes.exe` is a PDF; an executable named `lecture.pdf` is rejected as an executable.

Rejected outright: executables and scripts with a shebang (PE/MZ, ELF, Mach-O, Java class/JAR), legacy OLE2 Office
files (`.doc/.xls/.ppt`: macro carriers, and also the container of password-protected OOXML), macro-enabled OOXML
(`vbaProject.bin`, `macroEnabled` content types), SVG (scriptable), and anything unrecognised.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import filetype  # type: ignore[import-untyped]

from . import errors
from .errors import IngestRejected
from .kinds import AUDIO_MIMES, IMAGE_MIMES, KIND_MIME, VIDEO_MIMES

_EXEC_MAGIC = (
    b"MZ",  # PE / DOS
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",  # Mach-O 32
    b"\xfe\xed\xfa\xcf",  # Mach-O 64
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"#!",  # scripts
    b"\x00asm",  # WebAssembly
)
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
_TEXT_SNIFF = 64 * 1024
_ANKI_COLLECTIONS = ("collection.anki2", "collection.anki21", "collection.anki21b")
_MACRO_MARKERS = (b"macroEnabled", b"vbaProject", b"vnd.ms-office.vbaProject")


@dataclass(frozen=True, slots=True)
class Sniffed:
    kind: str  # an InputKind (never "link" or "paste")
    mime: str


def _ext(name: str | None) -> str:
    if not name or "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()[:10]


def _decode_text(head: bytes) -> str | None:
    """The head as text if it is text (UTF-8, or UTF-16 with a BOM); None for binary."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return head.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in head:
        return None
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the sniff boundary is still text.
        if exc.start >= len(head) - 4:
            return head[: exc.start].decode("utf-8", errors="strict")
        try:
            text = head.decode("cp1252")
        except UnicodeDecodeError:
            return None
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        return text if printable / max(1, len(text)) > 0.97 else None


def _delimited(text: str, sep: str) -> bool:
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    if len(lines) < 2:
        return False
    counts = [ln.count(sep) for ln in lines]
    return counts[0] >= 1 and sum(c == counts[0] for c in counts) / len(counts) >= 0.8


def _text_kind(text: str, ext: str) -> Sniffed:
    head = text.lstrip("﻿ \r\n\t")[:2048]
    low = head.lower()
    if head.startswith("{\\rtf"):
        return Sniffed("rtf", KIND_MIME["rtf"])
    if low.startswith("<svg") or ("<svg" in low[:400] and low.startswith("<?xml")):
        raise IngestRejected(errors.SVG_UNSUPPORTED)
    if low.startswith(("<!doctype html", "<html")) or ("<html" in low[:600] and "<body" in low):
        return Sniffed("html", KIND_MIME["html"])
    if (
        ext in ("csv", "tsv")
        or _delimited(text, "\t")
        or (ext not in ("md", "markdown", "txt") and _delimited(text, ","))
    ):
        return Sniffed("csv", KIND_MIME["csv"])
    if ext in ("md", "markdown"):
        return Sniffed("md", KIND_MIME["md"])
    return Sniffed("txt", KIND_MIME["txt"])


def _zip_kind(data: bytes) -> Sniffed:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    lowered = {n.lower() for n in names}
    if "meta-inf/manifest.mf" in lowered and any(n.endswith(".class") for n in lowered):
        raise IngestRejected(errors.EXECUTABLE)
    if any(n.endswith("vbaproject.bin") for n in lowered):
        raise IngestRejected(errors.MACRO_OFFICE)
    if "[content_types].xml" in lowered:
        try:
            info = zf.getinfo(next(n for n in names if n.lower() == "[content_types].xml"))
            if info.file_size > 2 * 1024 * 1024:
                raise IngestRejected(errors.ZIP_BOMB)
            ct = zf.read(info)
        except (KeyError, zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise IngestRejected(errors.UNREADABLE) from exc
        if any(m.lower() in ct.lower() for m in _MACRO_MARKERS):
            raise IngestRejected(errors.MACRO_OFFICE)
        if any(n.startswith("word/") for n in lowered):
            return Sniffed("docx", KIND_MIME["docx"])
        if any(n.startswith("ppt/") for n in lowered):
            return Sniffed("pptx", KIND_MIME["pptx"])
        if any(n.startswith("xl/") for n in lowered):
            return Sniffed("xlsx", KIND_MIME["xlsx"])
    if "mimetype" in lowered:
        try:
            info = zf.getinfo("mimetype")
            if info.file_size < 100 and zf.read(info).strip() == b"application/epub+zip":
                return Sniffed("epub", KIND_MIME["epub"])
        except (KeyError, zipfile.BadZipFile, RuntimeError, OSError):
            pass
    if any(n in lowered for n in _ANKI_COLLECTIONS):
        return Sniffed("anki", KIND_MIME["anki"])
    return Sniffed("zip", KIND_MIME["zip"])


def sniff(data: bytes, name: str | None = None) -> Sniffed:
    """The kind of `data`, or IngestRejected with a reason code."""
    if not data:
        raise IngestRejected(errors.EMPTY_FILE)
    head = data[:16]
    if head.startswith(_EXEC_MAGIC):
        raise IngestRejected(errors.EXECUTABLE)
    if head.startswith(_OLE2):
        raise IngestRejected(errors.LEGACY_OFFICE)
    if b"%PDF-" in data[:1024]:
        return Sniffed("pdf", KIND_MIME["pdf"])
    if head.startswith(_ZIP):
        return _zip_kind(data)
    guess = filetype.guess(data[:8192])
    if guess is not None:
        mime = str(guess.mime)
        if mime in ("image/heic", "image/heif") or guess.extension in ("heic", "heif"):
            return Sniffed("image", "image/heic")
        if mime in IMAGE_MIMES:
            return Sniffed("image", mime)
        if mime in AUDIO_MIMES or mime in ("audio/x-m4a", "audio/m4a", "audio/x-flac", "audio/flac"):
            return Sniffed("audio", {"audio/x-m4a": "audio/mp4", "audio/m4a": "audio/mp4"}.get(mime, mime))
        if mime in VIDEO_MIMES:
            return Sniffed("video", mime)
        if mime in ("application/rtf", "text/rtf"):
            return Sniffed("rtf", KIND_MIME["rtf"])
        if mime in ("video/x-m4v",):
            return Sniffed("video", "video/mp4")
        if mime in ("video/x-matroska", "video/x-msvideo", "video/x-flv", "video/mpeg"):
            raise IngestRejected(errors.UNSUPPORTED_TYPE)
        if mime.startswith(("application/x-executable", "application/x-msdownload", "application/x-mach-binary")):
            raise IngestRejected(errors.EXECUTABLE)
        if mime.startswith(("image/", "audio/", "video/", "application/", "font/")):
            raise IngestRejected(errors.UNSUPPORTED_TYPE)
    text = _decode_text(data[:_TEXT_SNIFF])
    if text is None:
        raise IngestRejected(errors.UNSUPPORTED_TYPE)
    return _text_kind(text, _ext(name))


__all__ = ["Sniffed", "sniff"]
