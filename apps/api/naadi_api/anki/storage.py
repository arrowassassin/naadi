"""The `user-uploads` bucket through the official Supabase client (service role; paths start with the user id)."""

from __future__ import annotations

from typing import Any

from ..chapters_media import configured_buckets
from ..settings import Settings


class SupabaseImageStore:
    """`ImageStore` over supabase-py. Raw `httpx` is not used: the SDK has upload and download."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = configured_buckets(settings).user_uploads

    async def _client(self) -> Any:
        from supabase import acreate_client

        s = self._settings
        if not s.supabase_url or s.supabase_service_role_key is None:
            raise RuntimeError("storage is not configured")
        return await acreate_client(s.supabase_url, s.supabase_service_role_key.get_secret_value())

    async def put(self, path: str, data: bytes) -> None:
        client = await self._client()
        await client.storage.from_(self._bucket).upload(
            path, data, {"content-type": "image/webp", "upsert": "true", "cache-control": "31536000"}
        )

    async def get(self, path: str) -> bytes | None:
        client = await self._client()
        return bytes(await client.storage.from_(self._bucket).download(path))
