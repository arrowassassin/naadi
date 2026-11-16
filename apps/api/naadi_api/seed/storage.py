"""Figure uploads to Supabase Storage through the official SDK (`supabase` -> `storage3`).

Against a Supabase project: `supabase.create_client(SUPABASE_URL, service_role_key).storage`. Against a bare
storage-api (local stack, CI) the same SDK's `SyncStorageClient` is pointed at it directly (`storage_url`).
Objects are compared by MD5 (the storage eTag of a single-part upload), so unchanged figures are not
re-uploaded and a missing object is always restored.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from storage3 import SyncStorageClient
from supabase import create_client

SVG_MIME = "image/svg+xml"
CACHE_CONTROL_S = "3600"
LIST_PAGE = 1000


class StorageUnavailable(RuntimeError):
    pass


class BucketApi(Protocol):
    def list(self, path: str | None = ..., options: Any = ...) -> list[dict[str, Any]]: ...
    def upload(self, path: str, file: bytes, file_options: Any = ...) -> Any: ...


@dataclass(frozen=True)
class StorageTarget:
    """Where figures go. `storage_url` set = a bare storage-api; otherwise `supabase_url` (a project)."""

    key: str
    supabase_url: str | None = None
    storage_url: str | None = None

    def client(self) -> SyncStorageClient:
        if self.storage_url:
            return SyncStorageClient(
                self.storage_url.rstrip("/") + "/", {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
            )
        if not self.supabase_url:
            raise StorageUnavailable("SUPABASE_URL (or a storage URL) is required to upload figures")
        storage: SyncStorageClient = create_client(self.supabase_url, self.key).storage
        return storage


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def list_folder(bucket: BucketApi, folder: str) -> dict[str, str]:
    """`{object name: md5}` for every object directly in `folder` (paged)."""
    out: dict[str, str] = {}
    offset = 0
    while True:
        page = bucket.list(folder, {"limit": LIST_PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
        for obj in page:
            meta = obj.get("metadata") or {}
            if obj.get("id") is None:
                continue  # a sub-folder placeholder
            out[str(obj["name"])] = str(meta.get("eTag", "")).strip('"').lower()
        if len(page) < LIST_PAGE:
            return out
        offset += LIST_PAGE


@dataclass
class UploadResult:
    uploaded: list[str]
    unchanged: list[str]


def sync_objects(
    bucket: BucketApi, folder: str, objects: Iterable[tuple[str, bytes]], *, dry_run: bool
) -> UploadResult:
    """Upload every `(file name, bytes)` whose stored MD5 differs or that is missing."""
    existing = list_folder(bucket, folder)
    result = UploadResult([], [])
    for name, data in objects:
        if existing.get(name) == md5_hex(data):
            result.unchanged.append(name)
            continue
        result.uploaded.append(name)
        if not dry_run:
            bucket.upload(
                f"{folder}/{name}",
                data,
                {"content-type": SVG_MIME, "cache-control": CACHE_CONTROL_S, "upsert": "true"},
            )
    return result
