"""Storage access of the export job: the official `storage3` client (through `supabase`), sync, run in worker
threads. Local stack and CI point it at a bare storage-api (`NAADI_STORAGE_URL`), the same way the seed importer
does; against the real project it is `SUPABASE_URL` with the service role key."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from storage3 import SyncStorageClient

from ..seed.storage import StorageTarget
from ..settings import Settings

LIST_PAGE = 1000
SIGNED_URL_SECONDS = 24 * 3600
EXPORT_FOLDER = "exports"


class StorageNotConfigured(RuntimeError):
    pass


def target_from(settings: Settings) -> StorageTarget | None:
    key = settings.supabase_service_role_key
    if settings.storage_url and settings.storage_service_key is not None:
        key = settings.storage_service_key
    if key is None:
        return None
    if settings.storage_url:
        return StorageTarget(key=key.get_secret_value(), storage_url=settings.storage_url)
    if settings.supabase_url:
        return StorageTarget(key=key.get_secret_value(), supabase_url=settings.supabase_url)
    return None


class ExportStorage:
    def __init__(self, target: StorageTarget | None) -> None:
        self._target = target
        self._client: SyncStorageClient | None = None

    @property
    def configured(self) -> bool:
        return self._target is not None

    def _bucket(self, name: str) -> Any:
        if self._target is None:
            raise StorageNotConfigured("storage is not configured")
        if self._client is None:
            self._client = self._target.client()
        return self._client.from_(name)

    # ── sync primitives (run in threads) ──
    def _list_tree(self, bucket: str, prefix: str, skip_first: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        stack = [prefix]
        while stack:
            folder = stack.pop()
            offset = 0
            while True:
                page = self._bucket(bucket).list(folder, {"limit": LIST_PAGE, "offset": offset})
                for obj in page:
                    name = str(obj["name"])
                    full = f"{folder}/{name}"
                    if obj.get("id") is None:  # a folder
                        if not (skip_first and folder == prefix and name == skip_first):
                            stack.append(full)
                        continue
                    size = (obj.get("metadata") or {}).get("size")
                    out.append({"path": full, "bytes": int(size) if size is not None else None})
                if len(page) < LIST_PAGE:
                    break
                offset += LIST_PAGE
        return out

    async def list_tree(self, bucket: str, prefix: str, skip_first: str | None = EXPORT_FOLDER) -> list[dict[str, Any]]:
        """Every object under `prefix` (recursively), except the top-level `skip_first` folder."""
        return await asyncio.to_thread(self._list_tree, bucket, prefix, skip_first)

    async def download(self, bucket: str, path: str) -> bytes:
        data: bytes = await asyncio.to_thread(self._bucket(bucket).download, path)
        return data

    async def upload(self, bucket: str, path: str, file: Path) -> None:
        def go() -> None:
            self._bucket(bucket).upload(
                path, file.read_bytes(), {"content-type": "application/zip", "upsert": "true", "cache-control": "60"}
            )

        await asyncio.to_thread(go)

    async def sign(self, bucket: str, path: str, expires_in: int) -> str:
        def go() -> str:
            resp = self._bucket(bucket).create_signed_url(path, expires_in)
            url = resp.get("signedURL") or resp.get("signedUrl")
            return str(url)

        return await asyncio.to_thread(go)

    async def remove(self, bucket: str, paths: list[str]) -> None:
        if paths:
            await asyncio.to_thread(self._bucket(bucket).remove, paths)
