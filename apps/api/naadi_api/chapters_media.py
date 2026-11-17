"""Resolve `media_assets.url` to a URL the browser can load, per environment.

`media_assets.url` holds either a bucket-relative Storage path (`content-public/figures/ch01-fig01.svg`) or,
for external media, an absolute `https://` URL. Storage paths are resolved here so the same seeded rows work
against the local stack, CI and the real project:

Bucket names come from `app_config.storage` (config/app_config.yaml); the defaults are shown here.

- `content-public/...`  -> the public object URL `{SUPABASE_URL}/storage/v1/object/public/content-public/...`
- `content-private/...` -> a signed URL (service role, short expiry), cached until shortly before it expires
- `user-uploads/{uid}/...` -> signed, but only for the signed-in owner (the first folder is their user id),
  because the service role would otherwise bypass Storage's own per-user policy
- `vault/...` and every other bucket -> never resolved (vault files never leave through content paths)
- anything else (relative junk, `http:`, `javascript:` ...) -> None, so the renderer shows "unavailable"

Signed URLs are cached per (bucket, path); expired entries are evicted and the cache is bounded. A sign call
runs outside any lock, and concurrent requests for the same object share one in-flight call (single flight).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

from .config_api.defaults import file_values
from .config_api.schemas import StorageBuckets
from .logs import get_logger
from .settings import Settings

log = get_logger("naadi.media")

SIGNED_URL_TTL_S = 3600
# A cached signed URL is reused only while it has at least this long left.
SIGNED_URL_MIN_LEFT_S = 600
# Upper bound on cached signed URLs (one per object); the soonest-expiring are dropped first.
SIGNED_URL_CACHE_MAX = 4096

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


def configured_buckets(settings: Settings) -> StorageBuckets:
    """The Storage bucket names from config/app_config.yaml (`storage`), validated."""
    return StorageBuckets.model_validate(file_values(settings.config_dir)["storage"])


CacheKey = tuple[str, str]


class MediaUrlResolver:
    """Per-process resolver with a small signed-URL cache (one Space container serves every request)."""

    def __init__(
        self,
        settings: Settings,
        signer: Signer | None = None,
        clock: Callable[[], float] = time.time,
        buckets: StorageBuckets | None = None,
    ):
        self._settings = settings
        self._signer = signer
        self._clock = clock
        b = buckets or configured_buckets(settings)
        self._public_bucket = b.content_public
        self._uploads_bucket = b.user_uploads
        self._signed_buckets = frozenset({b.content_private, b.user_uploads})
        self._cache: dict[CacheKey, tuple[str, float]] = {}
        # One future per object being signed right now; later callers await it instead of signing again.
        self._inflight: dict[CacheKey, asyncio.Future[str | None]] = {}

    def _signer_or_default(self) -> Signer | None:
        if self._signer is not None:
            return self._signer
        s = self._settings
        if not s.supabase_url:
            return None
        url = s.supabase_url
        # A bare storage-api (local stack / CI: NAADI_STORAGE_URL) only accepts its own service key; the browser
        # still reaches it through SUPABASE_URL/storage/v1 (the gateway), so signed paths are made absolute there.
        direct = s.storage_url if s.storage_url and s.storage_service_key is not None else None
        secret = s.storage_service_key if direct else s.supabase_service_role_key
        if secret is None:
            return None
        key = secret.get_secret_value()

        async def sign(bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
            if direct:
                from storage3 import AsyncStorageClient

                storage: Any = AsyncStorageClient(
                    direct.rstrip("/") + "/", {"apikey": key, "Authorization": f"Bearer {key}"}
                )
            else:
                from supabase import acreate_client

                storage = (await acreate_client(url, key)).storage
            rows = await storage.from_(bucket).create_signed_urls(paths, expires_in)
            out: dict[str, str] = {}
            for row in rows:
                path, signed = row.get("path"), row.get("signedURL") or row.get("signedUrl")
                if isinstance(path, str) and isinstance(signed, str) and signed:
                    # storage3 makes the URL absolute on ITS base (the direct host, which the browser and the CSP
                    # don't know), so rebase it onto the gateway like a relative path.
                    if direct and signed.startswith(direct.rstrip("/") + "/"):
                        signed = signed[len(direct.rstrip("/")) :]
                    out[path] = signed if "://" in signed else f"{url.rstrip('/')}/storage/v1{signed}"
            return out

        self._signer = sign
        return sign

    @property
    def cached_count(self) -> int:
        return len(self._cache)

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
            if sp.bucket == self._public_bucket:
                out[media_id] = public_object_url(supa, sp)
            elif sp.bucket in self._signed_buckets:
                if sp.bucket == self._uploads_bucket and sp.path.split("/", 1)[0] != str(owner):
                    log.warning("media_url_foreign_upload", media_id=media_id)
                    continue
                to_sign.setdefault(sp.bucket, []).append((media_id, sp.path))
            else:
                log.warning("media_url_bucket_refused", media_id=media_id, bucket=sp.bucket)
        if to_sign:
            await self._sign_into(out, to_sign)
        return out

    def _evict(self, now: float) -> None:
        for k in [k for k, (_, exp) in self._cache.items() if exp - now <= SIGNED_URL_MIN_LEFT_S]:
            del self._cache[k]
        over = len(self._cache) - SIGNED_URL_CACHE_MAX
        if over > 0:
            for k in sorted(self._cache, key=lambda k: self._cache[k][1])[:over]:
                del self._cache[k]

    async def _sign_into(self, out: dict[str, str | None], to_sign: dict[str, list[tuple[str, str]]]) -> None:
        # Everything up to the first await runs without yielding, so the cache and in-flight table need no lock.
        now = self._clock()
        self._evict(now)
        waits: dict[CacheKey, asyncio.Future[str | None]] = {}
        mine: dict[str, list[str]] = {}
        loop = asyncio.get_running_loop()
        for bucket, items in to_sign.items():
            for _media_id, path in items:
                key = (bucket, path)
                if key in waits or key in self._cache:
                    continue
                running = self._inflight.get(key)
                if running is None:
                    running = loop.create_future()
                    self._inflight[key] = running
                    mine.setdefault(bucket, []).append(path)
                waits[key] = running
        if mine:
            await asyncio.gather(*(self._sign_bucket(b, paths) for b, paths in mine.items()))
        for bucket, items in to_sign.items():
            for media_id, path in items:
                key = (bucket, path)
                hit = self._cache.get(key)
                if hit is not None:
                    out[media_id] = hit[0]
                elif key in waits:
                    # shield: one cancelled request must not cancel the shared result for the others
                    out[media_id] = await asyncio.shield(waits[key])

    async def _sign_bucket(self, bucket: str, paths: list[str]) -> None:
        """Sign `paths` (all registered as in flight by this call) and settle their futures, whatever happens."""
        signed: dict[str, str] = {}
        try:
            signer = self._signer_or_default()
            if signer is not None:
                try:
                    signed = await signer(bucket, sorted(paths), SIGNED_URL_TTL_S)
                except Exception as exc:  # noqa: BLE001 - one unreachable Storage must not fail the chapter
                    log.warning("media_sign_failed", bucket=bucket, error=type(exc).__name__)
            expires = self._clock() + SIGNED_URL_TTL_S
            for path in paths:
                url = signed.get(path)
                if url:
                    self._cache[(bucket, path)] = (url, expires)
        finally:
            for path in paths:
                fut = self._inflight.pop((bucket, path), None)
                if fut is not None and not fut.done():
                    fut.set_result(signed.get(path))
