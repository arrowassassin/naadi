"""Object storage for generated media (the official `supabase` SDK, service role; paths are built by the service).

`generated-media/{user_id}/{id}.webp|.mp4` is private: the browser only ever sees a signed URL that lasts ten
minutes. An admin approval copies an image to `content-public/generated/{media_id}.webp`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from ..settings import Settings

SIGNED_URL_TTL_S = 600


class MediaStoreError(Exception):
    """Storage refused or failed; the code is stable, the cause is never shown."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MediaStore(Protocol):
    async def put(self, bucket: str, path: str, data: bytes, mime: str) -> None: ...
    async def get(self, bucket: str, path: str) -> bytes: ...
    async def delete(self, bucket: str, paths: list[str]) -> None: ...
    async def sign(self, bucket: str, path: str, expires_in: int = SIGNED_URL_TTL_S) -> str | None: ...


class SupabaseMediaStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        s = self._settings
        return bool(s.supabase_url and s.supabase_service_role_key is not None)

    async def _storage(self, bucket: str) -> Any:
        s = self._settings
        if not self.configured or s.supabase_url is None or s.supabase_service_role_key is None:
            raise MediaStoreError("storage_not_configured")
        async with self._lock:
            if self._client is None:
                from supabase import acreate_client

                self._client = await acreate_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())
        return self._client.storage.from_(bucket)

    async def put(self, bucket: str, path: str, data: bytes, mime: str) -> None:
        try:
            await (await self._storage(bucket)).upload(path, data, {"content-type": mime, "upsert": "true"})
        except MediaStoreError:
            raise
        except Exception as exc:
            raise MediaStoreError("storage_write_failed") from exc

    async def get(self, bucket: str, path: str) -> bytes:
        try:
            data: bytes = await (await self._storage(bucket)).download(path)
        except MediaStoreError:
            raise
        except Exception as exc:
            raise MediaStoreError("storage_read_failed") from exc
        return data

    async def delete(self, bucket: str, paths: list[str]) -> None:
        try:
            await (await self._storage(bucket)).remove(paths)
        except MediaStoreError:
            raise
        except Exception as exc:
            raise MediaStoreError("storage_write_failed") from exc

    async def sign(self, bucket: str, path: str, expires_in: int = SIGNED_URL_TTL_S) -> str | None:
        try:
            row = await (await self._storage(bucket)).create_signed_url(path, expires_in)
        except MediaStoreError:
            raise
        except Exception as exc:
            raise MediaStoreError("storage_read_failed") from exc
        signed = row.get("signedURL") or row.get("signedUrl")
        if not isinstance(signed, str) or not signed:
            return None
        base = (self._settings.supabase_url or "").rstrip("/")
        return signed if "://" in signed else f"{base}/storage/v1{signed}"


class MemoryMediaStore:
    """In-memory store for tests and the non-production stub."""

    def __init__(self, base_url: str = "http://storage.test") -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.fail_put = False
        self._base = base_url.rstrip("/")

    async def put(self, bucket: str, path: str, data: bytes, mime: str) -> None:
        if self.fail_put:
            raise MediaStoreError("storage_write_failed")
        self.objects[(bucket, path)] = (data, mime)

    async def get(self, bucket: str, path: str) -> bytes:
        try:
            return self.objects[(bucket, path)][0]
        except KeyError as exc:
            raise MediaStoreError("storage_read_failed") from exc

    async def delete(self, bucket: str, paths: list[str]) -> None:
        for p in paths:
            self.objects.pop((bucket, p), None)

    async def sign(self, bucket: str, path: str, expires_in: int = SIGNED_URL_TTL_S) -> str | None:
        if (bucket, path) not in self.objects:
            return None
        return f"{self._base}/{bucket}/{path}?token=t&exp={expires_in}"


__all__ = ["SIGNED_URL_TTL_S", "MediaStore", "MediaStoreError", "MemoryMediaStore", "SupabaseMediaStore"]
