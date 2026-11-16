"""Resolve `media_assets.url` to a URL the browser can load, per environment.

`media_assets.url` holds either a bucket-relative Storage path (`content-public/figures/ch01-fig01.svg`) or,
for external media, an absolute `https://` URL. Storage paths are resolved here so the same seeded rows work
against the local stack, CI and the real project:

- `content-public/...`  -> the public object URL `{SUPABASE_URL}/storage/v1/object/public/content-public/...`
- `content-private/...` -> a signed URL (service role, short expiry), cached until shortly before it expires
- `user-uploads/{uid}/...` -> signed, but only for the signed-in owner (the first folder is their user id),
  because the service role would otherwise bypass Storage's own per-user policy
- `vault/...` and every other bucket -> never resolved (vault files never leave through content paths)
- anything else (relative junk, `http:`, `javascript:` ...) -> None, so the renderer shows "unavailable"
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import quote, urlparse

from .logs import get_logger
from .settings import Settings

log = get_logger("naadi.media")

PUBLIC_BUCKET = "content-public"
SIGNED_BUCKETS = frozenset({"content-private", "user-uploads"})
SIGNED_URL_TTL_S = 3600
# A cached signed URL is reused only while it has at least this long left.
SIGNED_URL_MIN_LEFT_S = 600

Signer = Callable[[str, list[str], int], Awaitable[dict[str, str]]]


@dataclass(frozen=True, slots=True)
class StoragePath:
    bucket: str
    path: str


def parse_storage_path(raw: str) -> StoragePath | None:
    """`bucket/path/to/object` -> StoragePath; None for absolute URLs, traversal or empty parts."""
    raw = raw.strip()
    if not raw or "://" in raw or raw.startswith(("/", "\\")) or "\\" in raw:
        return None
    bucket, sep, path = raw.partition("/")
    if not sep or not bucket or not path:
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return StoragePath(bucket, path)


def public_object_url(supabase_url: str, sp: StoragePath) -> str:
    base = supabase_url.rstrip("/")
    return f"{base}/storage/v1/object/public/{quote(sp.bucket)}/{quote(sp.path)}"


def external_url(raw: str) -> str | None:
    u = urlparse(raw.strip())
    if u.scheme == "https" and u.hostname:
        return raw.strip()
    return None


class MediaUrlResolver:
    """Per-process resolver with a small signed-URL cache (one Space container serves every request)."""

    def __init__(self, settings: Settings, signer: Signer | None = None, clock: Callable[[], float] = time.time):
        self._settings = settings
        self._signer = signer
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def _signer_or_default(self) -> Signer | None:
        if self._signer is not None:
            return self._signer
        s = self._settings
        if not s.supabase_url or s.supabase_service_role_key is None:
            return None
        url, key = s.supabase_url, s.supabase_service_role_key.get_secret_value()

        async def sign(bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
            from supabase import acreate_client

            client = await acreate_client(url, key)
            rows = await client.storage.from_(bucket).create_signed_urls(paths, expires_in)
            out: dict[str, str] = {}
            for row in rows:
                path, signed = row.get("path"), row.get("signedURL") or row.get("signedUrl")
                if isinstance(path, str) and isinstance(signed, str) and signed:
                    out[path] = signed if "://" in signed else f"{url.rstrip('/')}/storage/v1{signed}"
            return out

        self._signer = sign
        return sign

    async def resolve(self, raws: Iterable[tuple[str, str | None]], owner: uuid.UUID) -> dict[str, str | None]:
        """{media_id: raw url} -> {media_id: browser URL or None}. Never raises for one bad row."""
        out: dict[str, str | None] = {}
        to_sign: dict[str, list[tuple[str, str]]] = {}
        supa = self._settings.supabase_url
        for media_id, raw in raws:
            out[media_id] = None
            if not raw:
                continue
            sp = parse_storage_path(raw)
            if sp is None:
                ext = external_url(raw)
                if ext is None:
                    log.warning("media_url_unusable", media_id=media_id)
                out[media_id] = ext
                continue
            if not supa:
                continue
            if sp.bucket == PUBLIC_BUCKET:
                out[media_id] = public_object_url(supa, sp)
            elif sp.bucket in SIGNED_BUCKETS:
                if sp.bucket == "user-uploads" and sp.path.split("/", 1)[0] != str(owner):
                    log.warning("media_url_foreign_upload", media_id=media_id)
                    continue
                to_sign.setdefault(sp.bucket, []).append((media_id, sp.path))
            else:
                log.warning("media_url_bucket_refused", media_id=media_id, bucket=sp.bucket)
        if to_sign:
            await self._sign_into(out, to_sign)
        return out

    async def _sign_into(self, out: dict[str, str | None], to_sign: dict[str, list[tuple[str, str]]]) -> None:
        now = self._clock()
        async with self._lock:
            for bucket, items in to_sign.items():
                missing: list[str] = []
                for media_id, path in items:
                    hit = self._cache.get((bucket, path))
                    if hit and hit[1] - now > SIGNED_URL_MIN_LEFT_S:
                        out[media_id] = hit[0]
                    else:
                        missing.append(path)
                if not missing:
                    continue
                signer = self._signer_or_default()
                if signer is None:
                    continue
                try:
                    signed = await signer(bucket, sorted(set(missing)), SIGNED_URL_TTL_S)
                except Exception as exc:  # noqa: BLE001 - one unreachable Storage must not fail the chapter
                    log.warning("media_sign_failed", bucket=bucket, error=type(exc).__name__)
                    continue
                for path, url in signed.items():
                    self._cache[(bucket, path)] = (url, now + SIGNED_URL_TTL_S)
                for media_id, path in items:
                    if out.get(media_id) is None and path in signed:
                        out[media_id] = signed[path]
