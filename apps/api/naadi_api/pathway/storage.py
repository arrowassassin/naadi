"""The real `VaultStorage`: Supabase Storage through the official `supabase` client with the service role.

Imports nothing from the model, search or embeddings code (see vault.py). Storage errors never carry a path or a
byte into a log line: only the exception type."""

from __future__ import annotations

from typing import Any

from storage3.exceptions import StorageApiError
from supabase import AsyncClient, acreate_client

from ..logs import get_logger
from ..settings import Settings
from .db import PathwayError

log = get_logger("naadi.vault")


class SupabaseVaultStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or settings.supabase_service_role_key is None:
            raise PathwayError(503, "vault_unavailable")
        self._url = settings.supabase_url
        self._key = settings.supabase_service_role_key.get_secret_value()
        self._client: AsyncClient | None = None

    async def _storage(self) -> Any:
        if self._client is None:
            self._client = await acreate_client(self._url, self._key)
        return self._client.storage

    async def _call[T](self, op: str, coro: Any) -> T:
        try:
            return await coro  # type: ignore[no-any-return]
        except StorageApiError as exc:
            if _missing(exc):
                raise
            log.warning("vault_storage_error", op=op, status=getattr(exc, "status", None))
            raise PathwayError(502, "vault_storage_unavailable") from exc
        except Exception as exc:
            log.warning("vault_storage_error", op=op, error=type(exc).__name__)
            raise PathwayError(502, "vault_storage_unavailable") from exc

    async def create_signed_upload(self, path: str) -> tuple[str, str]:
        st = await self._storage()
        out: dict[str, Any] = await self._call("sign_upload", st.from_("vault").create_signed_upload_url(path))
        return str(out["signed_url"]), str(out["token"])

    async def download(self, bucket: str, path: str) -> bytes | None:
        st = await self._storage()
        try:
            data: bytes = await self._call("download", st.from_(bucket).download(path))
        except StorageApiError as exc:
            if _missing(exc):
                return None
            raise
        return bytes(data)

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        st = await self._storage()
        await self._call("upload", st.from_("vault").upload(path, data, {"content-type": content_type}))

    async def signed_url(self, path: str, seconds: int) -> str:
        st = await self._storage()
        out: dict[str, Any] = await self._call("sign", st.from_("vault").create_signed_url(path, seconds))
        url = out.get("signedURL") or out.get("signedUrl")
        if not url:
            raise PathwayError(502, "vault_storage_unavailable")
        return str(url)

    async def remove(self, bucket: str, path: str) -> None:
        st = await self._storage()
        await self._call("remove", st.from_(bucket).remove([path]))


def _missing(exc: StorageApiError) -> bool:
    status = str(getattr(exc, "status", "") or "")
    code = str(getattr(exc, "code", "") or "")
    return status in {"404", "400"} and ("not" in code.lower() or "found" in str(exc).lower() or status == "404")
