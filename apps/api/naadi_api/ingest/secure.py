"""Caps and archive guards applied before any parser sees the bytes.

- size: `max_file_mb` per file;
- ZIP (bundles, and the OOXML/EPUB containers): entry count, total declared uncompressed size, per-entry and overall
  compression ratio (a bomb), encrypted entries, absolute or `..` paths, symlinks, nesting depth;
- reading a member never trusts the declared size: `read_member` stops one byte past the cap.

PDF encryption and image dimensions are checked inside the extraction sandbox (they need a parser).
"""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

from ..config_api.schemas import IngestRules
from . import errors
from .errors import IngestRejected

MB = 1024 * 1024
_CHUNK = 64 * 1024
# OOXML/EPUB members are small; a container whose parts expand past this is treated as a bomb.
CONTAINER_MAX_UNCOMPRESSED = 400 * MB
_SKIP_PREFIXES = ("__macosx/", ".")


def check_size(n: int, rules: IngestRules) -> None:
    if n <= 0:
        raise IngestRejected(errors.EMPTY_FILE)
    if n > rules.max_file_mb * MB:
        raise IngestRejected(errors.FILE_TOO_LARGE, limit_mb=rules.max_file_mb)


@dataclass(frozen=True, slots=True)
class Member:
    name: str  # normalised POSIX path inside the archive
    size: int  # declared uncompressed size
    info: zipfile.ZipInfo


def _safe_name(name: str) -> str | None:
    """The normalised member path, or None when it must be skipped (directories, OS metadata)."""
    if "\\" in name:
        name = name.replace("\\", "/")
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or (name[:2].endswith(":")):
        raise IngestRejected(errors.ZIP_UNSAFE_PATH)
    if name.endswith("/"):
        return None
    low = name.lower()
    if low.startswith(_SKIP_PREFIXES) or "/__macosx/" in low or p.name.startswith("._") or p.name in (".DS_Store",):
        return None
    return str(p)


def _ratio_ok(info: zipfile.ZipInfo, max_ratio: int) -> bool:
    if info.file_size == 0:
        return True
    return info.file_size <= max(info.compress_size, 1) * max_ratio


def inspect_container(data: bytes, *, max_ratio: int) -> None:
    """Guards for OOXML/EPUB/Anki containers (read by parsers, not unpacked by us)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise IngestRejected(errors.ENCRYPTED_ARCHIVE)
        if not _ratio_ok(info, max_ratio):
            raise IngestRejected(errors.ZIP_BOMB)
        total += info.file_size
    if total > CONTAINER_MAX_UNCOMPRESSED or len(infos) > 20_000:
        raise IngestRejected(errors.ZIP_BOMB)


def list_bundle(data: bytes, rules: IngestRules) -> list[Member]:
    """The files of a ZIP bundle that will each be ingested, after every guard."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    if len(infos) > rules.zip_max_entries * 2:
        raise IngestRejected(errors.ZIP_TOO_MANY, limit=rules.zip_max_entries)
    members: list[Member] = []
    total = 0
    compressed = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise IngestRejected(errors.ENCRYPTED_ARCHIVE)
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise IngestRejected(errors.ZIP_UNSAFE_PATH)
        name = _safe_name(info.filename)
        if name is None:
            continue
        if not _ratio_ok(info, rules.zip_max_ratio):
            raise IngestRejected(errors.ZIP_BOMB)
        if info.file_size > rules.max_file_mb * MB:
            raise IngestRejected(errors.FILE_TOO_LARGE, limit_mb=rules.max_file_mb)
        total += info.file_size
        compressed += info.compress_size
        members.append(Member(name, info.file_size, info))
    if len(members) > rules.zip_max_entries:
        raise IngestRejected(errors.ZIP_TOO_MANY, limit=rules.zip_max_entries)
    if total > rules.zip_max_uncompressed_mb * MB:
        raise IngestRejected(errors.ZIP_TOO_LARGE, limit_mb=rules.zip_max_uncompressed_mb)
    if total > max(compressed, 1) * rules.zip_max_ratio:
        raise IngestRejected(errors.ZIP_BOMB)
    return members


def read_member(data: bytes, member: Member, cap: int) -> bytes:
    """A member's bytes, refusing to inflate past `cap` (or past its declared size) whatever the headers claim."""
    limit = min(cap, member.size)
    out = io.BytesIO()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open(member.info) as fh:
            for chunk in _chunks(fh):
                out.write(chunk)
                if out.tell() > limit:
                    raise IngestRejected(errors.ZIP_BOMB)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise IngestRejected(errors.UNREADABLE) from exc
    return out.getvalue()


def _chunks(fh: IO[bytes]) -> Iterator[bytes]:
    while True:
        b = fh.read(_CHUNK)
        if not b:
            return
        yield b


__all__ = ["Member", "check_size", "inspect_container", "list_bundle", "read_member"]
