"""Build the export ZIP: pure over two small sources, so it is tested without a database or storage.

Layout of part 1 (`{id}.zip`):  `manifest.json`, `data/<table>.json` (one JSON array per user table) and as many
files as fit; later parts (`{id}-2.zip`, ...) hold more files only. Files are the student's own uploads and
generated media, and the vault only when `include_vault` is set. Shared content (chapters, questions, answer keys)
is never part of an export. A file larger than one part cannot be zipped (the storage bucket caps every object);
it is listed under `skipped` and stays downloadable from Library.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

MB = 1024 * 1024
PART_LIMIT = 40 * MB  # the user-uploads bucket caps an object at 50 MB; leave room for ZIP overhead
FORMAT_VERSION = 1
AREAS = ("uploads", "generated-media", "vault")
_STORED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".mp4", ".webm", ".mp3", ".zip", ".heic", ".pdf"}


@dataclass(frozen=True)
class FileEntry:
    area: str  # uploads | generated-media | vault
    bucket: str
    path: str  # object path inside the bucket (`{user_id}/...`)
    name: str  # path below the area in the ZIP
    bytes: int | None = None


class Source(Protocol):
    def tables(self, include_vault: bool) -> Sequence[str]: ...
    def rows(self, table: str) -> AsyncIterator[dict[str, Any]]: ...
    async def files(self, include_vault: bool) -> Sequence[FileEntry]: ...
    async def read(self, entry: FileEntry) -> bytes: ...


@dataclass
class Built:
    parts: list[Path]
    manifest: dict[str, Any]
    bytes: int = 0
    tmpdir: Path | None = None
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


class _Part:
    def __init__(self, directory: Path, name: str) -> None:
        self.path = directory / name
        self.zf = zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED)

    @property
    def size(self) -> int:
        """Bytes written so far (compressed): what the storage bucket's object limit counts."""
        fp = self.zf.fp
        return fp.tell() if fp is not None else 0

    def add(self, name: str, data: bytes) -> None:
        ext = Path(name).suffix.lower()
        self.zf.writestr(name, data, compress_type=zipfile.ZIP_STORED if ext in _STORED else zipfile.ZIP_DEFLATED)

    def close(self) -> None:
        self.zf.close()


async def build_export(
    source: Source,
    *,
    user_id: str,
    include_vault: bool,
    job_id: str,
    part_limit: int = PART_LIMIT,
    now: datetime | None = None,
) -> Built:
    """Write the ZIP part(s) into a fresh temp directory (the caller uploads them, then deletes `tmpdir`)."""
    tmp = Path(tempfile.mkdtemp(prefix="naadi-export-"))
    part1 = _Part(tmp, f"{job_id}.zip")
    parts: list[_Part] = [part1]
    table_counts: dict[str, int] = {}
    for table in source.tables(include_vault):
        n = 0
        with part1.zf.open(f"data/{table}.json", "w", force_zip64=True) as out:
            out.write(b"[")
            async for row in source.rows(table):
                out.write((b"," if n else b"") + json.dumps(row, ensure_ascii=False, default=_json_default).encode())
                n += 1
            out.write(b"]")
        table_counts[table] = n
    files_meta: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    current = part1
    reserve = MB  # the manifest goes into part 1 last
    for entry in sorted(await source.files(include_vault), key=lambda e: (e.area, e.name)):
        if entry.bytes is not None and entry.bytes > part_limit - reserve:
            skipped.append({"area": entry.area, "name": entry.name, "bytes": entry.bytes, "reason": "too_large"})
            continue
        data = await source.read(entry)
        if len(data) > part_limit - reserve:
            skipped.append({"area": entry.area, "name": entry.name, "bytes": len(data), "reason": "too_large"})
            continue
        if current.size + len(data) > part_limit - reserve and (current is not part1 or files_meta):
            if current is not part1:
                current.close()
            current = _Part(tmp, f"{job_id}-{len(parts) + 1}.zip")
            parts.append(current)
        zip_name = f"files/{entry.area}/{entry.name}"
        current.add(zip_name, data)
        files_meta.append(
            {
                "area": entry.area,
                "name": zip_name,
                "part": parts.index(current) + 1,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "user_id": user_id,
        "include_vault": include_vault,
        "tables": table_counts,
        "files": files_meta,
        "skipped": skipped,
        "parts": len(parts),
        "notes_pdfs": {"made_on": "device"},
    }
    part1.zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    for p in parts:
        p.close()
    total = sum(p.path.stat().st_size for p in parts)
    return Built([p.path for p in parts], manifest, total, tmp, skipped)
