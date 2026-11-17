"""The export ZIP: manifest, one JSON array per table, files by area, the vault only when ticked, part splitting
and what is never in it. No database, no storage."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import AsyncIterator, Sequence
from typing import Any

from naadi_api.export.builder import MB, FileEntry, build_export

UID = "11111111-1111-1111-1111-111111111111"


class FakeSource:
    def __init__(self, files: dict[str, bytes] | None = None, vault_table: bool = True) -> None:
        self.data: dict[str, list[dict[str, Any]]] = {
            "attempts": [{"id": 1, "question_id": "q1", "chosen": "B"}, {"id": 2, "question_id": "q2", "chosen": "C"}],
            "notes": [{"id": "n1", "body": {"type": "doc"}, "title": "Ünïcode ✓"}],
            "profiles": [],
        }
        if vault_table:
            self.data["vault_documents"] = [{"id": "v1", "title": "Passport"}]
        self.blobs = files or {}
        self.reads: list[str] = []

    def tables(self, include_vault: bool) -> Sequence[str]:
        return [t for t in self.data if include_vault or t != "vault_documents"]

    async def rows(self, table: str) -> AsyncIterator[dict[str, Any]]:
        for r in self.data[table]:
            yield r

    async def files(self, include_vault: bool) -> Sequence[FileEntry]:
        out = []
        for path, blob in self.blobs.items():
            area = path.split("/", 1)[0]
            if area == "vault" and not include_vault:
                continue
            out.append(FileEntry(area, f"bucket-{area}", f"{UID}/{path}", path.split("/", 1)[1], len(blob)))
        return out

    async def read(self, entry: FileEntry) -> bytes:
        self.reads.append(entry.name)
        return self.blobs[f"{entry.area}/{entry.name}"]


async def build(src: FakeSource, **kw: Any) -> tuple[Any, list[zipfile.ZipFile]]:
    built = await build_export(src, user_id=UID, job_id="job1", **kw)
    return built, [zipfile.ZipFile(p) for p in built.parts]


async def test_the_zip_holds_a_manifest_and_one_json_array_per_user_table() -> None:
    built, zips = await build(FakeSource(), include_vault=False)
    try:
        z = zips[0]
        assert sorted(z.namelist()) == ["data/attempts.json", "data/notes.json", "data/profiles.json", "manifest.json"]
        assert json.loads(z.read("data/attempts.json")) == [
            {"id": 1, "question_id": "q1", "chosen": "B"},
            {"id": 2, "question_id": "q2", "chosen": "C"},
        ]
        assert json.loads(z.read("data/notes.json"))[0]["title"] == "Ünïcode ✓"
        assert json.loads(z.read("data/profiles.json")) == []
        m = json.loads(z.read("manifest.json"))
        assert m["format_version"] == 1 and m["user_id"] == UID and m["include_vault"] is False
        assert m["tables"] == {"attempts": 2, "notes": 1, "profiles": 0}
        assert m["parts"] == 1 and m["skipped"] == [] and m["notes_pdfs"] == {"made_on": "device"}
    finally:
        shutil.rmtree(built.tmpdir)


async def test_files_are_included_by_area_with_their_checksums() -> None:
    src = FakeSource({"uploads/a/notes.pdf": b"%PDF-1", "generated-media/img.png": b"\x89PNG"})
    built, zips = await build(src, include_vault=False)
    try:
        z = zips[0]
        assert (
            z.read("files/uploads/a/notes.pdf") == b"%PDF-1" and z.read("files/generated-media/img.png") == b"\x89PNG"
        )
        m = json.loads(z.read("manifest.json"))
        assert [(f["name"], f["part"], f["bytes"]) for f in m["files"]] == [
            ("files/generated-media/img.png", 1, 4),
            ("files/uploads/a/notes.pdf", 1, 6),
        ]
        assert all(len(f["sha256"]) == 64 for f in m["files"])
    finally:
        shutil.rmtree(built.tmpdir)


async def test_the_vault_is_included_only_when_ticked() -> None:
    blobs = {"uploads/x.txt": b"x", "vault/passport.pdf": b"SECRET"}
    off_src = FakeSource(blobs)
    built, zips = await build(off_src, include_vault=False)
    try:
        assert not any("vault" in n for n in zips[0].namelist())
        assert "vault/passport.pdf" not in off_src.reads and b"SECRET" not in b"".join(
            p.read_bytes() for p in built.parts
        )
        assert "vault_documents" not in json.loads(zips[0].read("manifest.json"))["tables"]
    finally:
        shutil.rmtree(built.tmpdir)
    built, zips = await build(FakeSource(blobs), include_vault=True)
    try:
        assert zips[0].read("files/vault/passport.pdf") == b"SECRET"
        assert json.loads(zips[0].read("data/vault_documents.json")) == [{"id": "v1", "title": "Passport"}]
        assert json.loads(zips[0].read("manifest.json"))["include_vault"] is True
    finally:
        shutil.rmtree(built.tmpdir)


async def test_large_exports_are_split_into_parts_under_the_limit() -> None:
    import os

    blobs = {f"uploads/f{i}.bin": os.urandom(300_000) for i in range(5)}  # incompressible
    built, zips = await build(FakeSource(blobs), include_vault=False, part_limit=900_000 + MB)
    try:
        assert len(built.parts) >= 2
        assert all(p.stat().st_size <= 900_000 + MB for p in built.parts)
        m = json.loads(zips[0].read("manifest.json"))
        assert m["parts"] == len(built.parts) == len(zips)
        by_part = {f["name"]: f["part"] for f in m["files"]}
        assert sorted(by_part) == [f"files/uploads/f{i}.bin" for i in range(5)]
        for name, part in by_part.items():
            assert name in zips[part - 1].namelist()
        assert built.parts[1].name == "job1-2.zip" and built.parts[0].name == "job1.zip"
    finally:
        shutil.rmtree(built.tmpdir)


async def test_a_file_bigger_than_a_part_is_listed_as_skipped_not_dropped_silently() -> None:
    src = FakeSource({"uploads/huge.mp4": b"0" * 50, "uploads/small.txt": b"ok"})
    built, zips = await build(src, include_vault=False, part_limit=MB + 10)
    try:
        m = json.loads(zips[0].read("manifest.json"))
        assert m["skipped"] == [{"area": "uploads", "name": "huge.mp4", "bytes": 50, "reason": "too_large"}]
        assert "files/uploads/small.txt" in zips[0].namelist() and "files/uploads/huge.mp4" not in zips[0].namelist()
    finally:
        shutil.rmtree(built.tmpdir)
    # the listed size alone is enough to skip: the file is never downloaded
    src = FakeSource({"uploads/huge.mp4": b"0" * 20})
    entry_sizes = {"huge.mp4": 5 * MB}

    async def files(include_vault: bool) -> list[FileEntry]:
        return [FileEntry("uploads", "b", f"{UID}/huge.mp4", "huge.mp4", entry_sizes["huge.mp4"])]

    src.files = files  # type: ignore[method-assign]
    built, zips = await build(src, include_vault=False, part_limit=MB + 10)
    try:
        assert src.reads == [] and json.loads(zips[0].read("manifest.json"))["skipped"][0]["reason"] == "too_large"
    finally:
        shutil.rmtree(built.tmpdir)


async def test_the_zip_is_valid_and_readable_end_to_end() -> None:
    built, zips = await build(FakeSource({"uploads/a.txt": b"hello"}), include_vault=False)
    try:
        assert all(z.testzip() is None for z in zips)
        buf = io.BytesIO(built.parts[0].read_bytes())
        assert zipfile.is_zipfile(buf)
    finally:
        shutil.rmtree(built.tmpdir)
