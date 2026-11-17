"""Supabase Storage for ingest, through the official SDK (`supabase` -> `storage3`'s AsyncStorageClient).

Paths: `user-uploads/{user_id}/{item_id}/original` (the file as uploaded),
`user-uploads/{user_id}/{item_id}/fig-3.webp` (figures, the converted image, a thumbnail),
`vault/{user_id}/{item_id}.<ext>` (a vaulted file). Every path starts with the owner's id, which is also what
Storage's per-user folder policy checks. The service role is used here because the pipeline runs in the background;
every method takes the owner and builds the path itself, so no caller can name a foreign folder. `MemoryStorage`
implements the same protocol for unit tests (no network).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..settings import Settings

SIGNED_UPLOAD_TTL_S = 7200  # storage-api's fixed lifetime for signed upload URLs
SIGNED_READ_TTL_S = 600


class StorageError(RuntimeError):
    pass


class StorageTooLarge(StorageError):
    """The object is larger than the caller allows (never read past the cap)."""


def upload_path(user_id: uuid.UUID, item_id: uuid.UUID) -> str:
    return f"{user_id}/{item_id}/original"


def asset_path(user_id: uuid.UUID, item_id: uuid.UUID, key: str, ext: str = "webp") -> str:
    return f"{user_id}/{item_id}/{key}.{ext}"


def vault_path(user_id: uuid.UUID, item_id: uuid.UUID, ext: str) -> str:
    return f"{user_id}/{item_id}.{ext}"


class Storage(Protocol):
    async def signed_upload(self, bucket: str, path: str) -> dict[str, str]: ...
    async def download(self, bucket: str, path: str, max_bytes: int) -> bytes: ...
    async def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None: ...
    async def remove(self, bucket: str, paths: list[str]) -> None: ...
    async def signed_url(self, bucket: str, path: str, ttl_s: int = SIGNED_READ_TTL_S) -> str: ...
    async def size(self, bucket: str, path: str) -> int | None: ...
    async def list_prefix(self, bucket: str, folder: str) -> list[str]: ...


class SupabaseStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or settings.supabase_service_role_key is None:
            raise StorageError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for uploads")
        self._base = settings.supabase_url.rstrip("/")
        self._key = settings.supabase_service_role_key
        self._client: Any = None

    def _storage(self) -> Any:
        if self._client is None:
            from storage3 import AsyncStorageClient

            key = self._key.get_secret_value()
            self._client = AsyncStorageClient(
                f"{self._base}/storage/v1/", {"apikey": key, "Authorization": f"Bearer {key}"}, timeout=120
            )
        return self._client

    def _abs(self, url: str) -> str:
        return url if "://" in url else f"{self._base}/storage/v1{url}"

    async def signed_upload(self, bucket: str, path: str) -> dict[str, str]:
        res = await self._storage().from_(bucket).create_signed_upload_url(path)
        return {"url": self._abs(str(res["signed_url"])), "token": str(res["token"]), "path": path}

    async def download(self, bucket: str, path: str, max_bytes: int) -> bytes:
        try:
            data = await self._storage().from_(bucket).download(path)
        except Exception as exc:
            raise StorageError("object not found") from exc
        if len(data) > max_bytes:
            raise StorageTooLarge("object larger than allowed")
        return bytes(data)

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        await (
            self._storage()
            .from_(bucket)
            .upload(path, data, {"content-type": content_type, "cache-control": "3600", "upsert": "true"})
        )

    async def remove(self, bucket: str, paths: list[str]) -> None:
        if paths:
            await self._storage().from_(bucket).remove(paths)

    async def signed_url(self, bucket: str, path: str, ttl_s: int = SIGNED_READ_TTL_S) -> str:
        res = await self._storage().from_(bucket).create_signed_url(path, ttl_s)
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        if not isinstance(url, str) or not url:
            raise StorageError("no signed URL")
        return self._abs(url)

    async def size(self, bucket: str, path: str) -> int | None:
        try:
            info = await self._storage().from_(bucket).info(path)
        except Exception:  # noqa: BLE001
            return None
        size = info.get("size") if isinstance(info, dict) else getattr(info, "size", None)
        return int(size) if size is not None else None

    async def list_prefix(self, bucket: str, folder: str) -> list[str]:
        """Object paths directly inside `folder` (an item's folder holds no sub-folders)."""
        out: list[str] = []
        offset = 0
        while True:
            page = await self._storage().from_(bucket).list(folder, {"limit": 1000, "offset": offset})
            names = [f"{folder.rstrip('/')}/{e['name']}" for e in page if isinstance(e, dict) and e.get("id")]
            out.extend(names)
            if len(page) < 1000:
                return out
            offset += 1000


@dataclass
class MemoryStorage:
    """In-memory Storage for tests."""

    objects: dict[tuple[str, str], tuple[bytes, str]] = field(default_factory=dict)

    async def signed_upload(self, bucket: str, path: str) -> dict[str, str]:
        return {"url": f"memory://{bucket}/{path}?token=t", "token": "t", "path": path}

    async def download(self, bucket: str, path: str, max_bytes: int) -> bytes:
        if (bucket, path) not in self.objects:
            raise StorageError("object not found")
        data = self.objects[(bucket, path)][0]
        if len(data) > max_bytes:
            raise StorageTooLarge("object larger than allowed")
        return data

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self.objects[(bucket, path)] = (data, content_type)

    async def remove(self, bucket: str, paths: list[str]) -> None:
        for p in paths:
            self.objects.pop((bucket, p), None)

    async def signed_url(self, bucket: str, path: str, ttl_s: int = SIGNED_READ_TTL_S) -> str:
        return f"memory://{bucket}/{path}?ttl={ttl_s}"

    async def size(self, bucket: str, path: str) -> int | None:
        obj = self.objects.get((bucket, path))
        return len(obj[0]) if obj else None

    async def list_prefix(self, bucket: str, folder: str) -> list[str]:
        prefix = folder.rstrip("/") + "/"
        return sorted(
            p for b, p in self.objects if b == bucket and p.startswith(prefix) and "/" not in p[len(prefix) :]
        )


_storage: Storage | None = None


def set_storage(s: Storage | None) -> None:
    global _storage
    _storage = s


def get_storage(settings: Settings) -> Storage:
    global _storage
    if _storage is None:
        _storage = SupabaseStorage(settings)
    return _storage


__all__ = [
    "MemoryStorage",
    "Storage",
    "StorageError",
    "StorageTooLarge",
    "SupabaseStorage",
    "asset_path",
    "get_storage",
    "set_storage",
    "upload_path",
    "vault_path",
]
