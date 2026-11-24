"""Admin media library (segment 19): figure and PDF uploads, YouTube by URL, review, section attachments.

- Upload: `POST /api/admin/media/files` with the raw file as the body (Content-Type names it; no multipart parser
  is needed) and the metadata as query parameters. Every upload names a `sources` row, and the licence gate for
  hosting a copy runs BEFORE anything is stored. SVG goes through the seed's sanitiser (`seed/svg.py`); PNG/JPEG/
  WebP/GIF are re-encoded to WebP with Pillow (metadata dropped, size bounded); PDFs are magic-checked and go to
  `content-private` (served by signed URL). New assets start `unreviewed` (invisible to students: RLS shows only
  `approved` media).
- YouTube: `lookup` reads oEmbed (and `videos.list` when a YouTube API key is configured) server-side: title,
  channel, embeddable, duration. `add` stores a `video_youtube` row; nothing is downloaded or re-hosted, and the
  student player is the official IFrame player (ContentRenderer).
- Review: approve runs the gates for the asset's use (host or embed) and for a page; reject keeps the row.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..chapters_media import MediaUrlResolver, parse_storage_path
from ..errors import ErrorResponse
from ..export.storage import target_from
from ..logs import get_logger
from ..seed.storage import StorageTarget
from ..seed.svg import SvgError, sanitize_svg
from .common import ADMIN_ERRORS, CONFLICT, INVALID, NOT_FOUND, ConsoleAdmin, Row, audit, not_found, one, rows, run_tx
from .licence import AssetFacts, GateRefused, Use, check_asset, check_media_row, media_licence

log = get_logger("naadi.admin.media")

router = APIRouter(prefix="/api/admin", tags=["admin"])

MEDIA_ID = r"^[a-z0-9][a-z0-9_-]{0,127}$"
SECTION_ID = r"^[a-z0-9][a-z0-9_-]{1,127}$"
SOURCE_ID = r"^[a-z0-9][a-z0-9:._-]{0,127}$"
TAXONOMY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
MAX_FIGURE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_RASTER_SIDE = 4096
# 40 MP decodes to at most 160 MB RGBA: a bound the free Space survives (a 10 MB file can claim far more).
MAX_RASTER_PIXELS = 40_000_000
WEBP_QUALITY = 85
RASTER_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
FIGURE_FOLDER = "figures/admin"
# Unreviewed figures wait in the private bucket; approval publishes them to FIGURE_FOLDER, rejection takes them back.
STAGING_FOLDER = "staging/figures"
PDF_FOLDER = "pdfs/admin"
CONTENT_TYPES = {
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"})
MediaKind = Literal["svg", "image", "video_own", "video_youtube", "audio", "pdf", "link", "video_file"]
ReviewStatus = Literal["unreviewed", "approved", "rejected"]


# ── storage (official SDK; injectable for tests) ──


class ObjectStore(Protocol):
    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None: ...
    def get(self, bucket: str, path: str) -> bytes: ...
    def delete(self, bucket: str, path: str) -> None: ...


@dataclass
class SupabaseObjectStore:
    target: StorageTarget

    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self.target.client().from_(bucket).upload(
            path, data, {"content-type": content_type, "cache-control": "3600", "upsert": "true"}
        )

    def get(self, bucket: str, path: str) -> bytes:
        data: bytes = self.target.client().from_(bucket).download(path)
        return data

    def delete(self, bucket: str, path: str) -> None:
        self.target.client().from_(bucket).remove([path])


def object_store_of(app: Any) -> ObjectStore | None:
    store: ObjectStore | None = getattr(app.state, "admin_object_store", None)
    if store is not None:
        return store
    # A bare storage-api (local stack / CI: NAADI_STORAGE_URL with its own service key) or the Supabase project.
    target = target_from(app.state.settings)
    return SupabaseObjectStore(target) if target else None


def object_store(request: Request) -> ObjectStore | None:
    return object_store_of(request.app)


# ── YouTube metadata (server-side; injectable for tests) ──


@dataclass(frozen=True)
class VideoMeta:
    youtube_id: str
    title: str
    channel_id: str | None
    channel_title: str | None
    embeddable: bool
    duration_s: int | None


class VideoLookup(Protocol):
    async def __call__(self, youtube_id: str) -> VideoMeta | None: ...


_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def iso_duration(v: str | None) -> int | None:
    m = _ISO_DURATION.match(v or "")
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


async def default_video_lookup(request: Request, youtube_id: str) -> VideoMeta | None:
    """oEmbed (no key: title, channel name; a 401/403 means embedding is disabled) plus YouTube Data API
    `videos.list` (`status.embeddable`, `contentDetails.duration`, channel id) when a key is configured.
    Raw httpx: neither endpoint has an official Python SDK in this stack (google-api-python-client is not a
    dependency and would be the only use)."""
    settings = request.app.state.settings
    key = getattr(settings, "youtube_api_key", None)
    watch = f"https://www.youtube.com/watch?v={youtube_id}"
    async with httpx.AsyncClient(timeout=8.0) as client:
        o = await client.get("https://www.youtube.com/oembed", params={"url": watch, "format": "json"})
        if o.status_code == 404:
            return None
        oembed = o.json() if o.status_code == 200 else {}
        embeddable = o.status_code == 200
        channel_id: str | None = None
        duration: int | None = None
        title = str(oembed.get("title") or "")
        channel_title = oembed.get("author_name")
        if key is not None:
            v = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "snippet,status,contentDetails", "id": youtube_id, "key": key.get_secret_value()},
            )
            items = v.json().get("items", []) if v.status_code == 200 else []
            if v.status_code == 200 and not items:
                return None
            if items:
                it = items[0]
                embeddable = bool(it.get("status", {}).get("embeddable"))
                channel_id = it.get("snippet", {}).get("channelId")
                channel_title = it.get("snippet", {}).get("channelTitle") or channel_title
                title = it.get("snippet", {}).get("title") or title
                duration = iso_duration(it.get("contentDetails", {}).get("duration"))
        if not title and not embeddable:
            return None
    return VideoMeta(youtube_id, title[:300], channel_id, (channel_title or None), embeddable, duration)


async def lookup_video(request: Request, youtube_id: str) -> VideoMeta | None:
    fake: VideoLookup | None = getattr(request.app.state, "admin_video_lookup", None)
    try:
        return await (fake(youtube_id) if fake else default_video_lookup(request, youtube_id))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("youtube_lookup_failed", error=type(exc).__name__)
        raise HTTPException(503, "YouTube cannot be reached right now. Try again in a moment.") from exc


def youtube_id_of(url: str) -> str | None:
    """The 11-character video id of a YouTube watch / share / embed / shorts URL; None for anything else."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in YOUTUBE_HOSTS:
        return None
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        cand = u.path.lstrip("/").split("/")[0]
    elif u.path == "/watch":
        cand = (parse_qs(u.query).get("v") or [""])[0]
    else:
        parts = [p for p in u.path.split("/") if p]
        cand = parts[1] if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v") else ""
    return cand if YOUTUBE_ID.match(cand) else None


# ── models ──


class AdminMedia(BaseModel):
    id: str
    kind: MediaKind
    title: str | None
    caption: str | None
    source_id: str
    review_status: ReviewStatus
    preview_url: str | None = Field(description="Public URL or short-lived signed URL for the admin preview")
    youtube_id: str | None
    channel_id: str | None
    embeddable: bool | None
    duration_s: int | None
    width: int | None
    height: int | None
    attribution: str | None
    taxonomy_ids: list[str]
    section_ids: list[str]
    metadata_fetched_at: dt.datetime | None


class AdminMediaList(BaseModel):
    items: list[AdminMedia]
    next_cursor: str | None


class VideoLookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=300)


class VideoLookupOut(BaseModel):
    youtube_id: str
    title: str
    channel_id: str | None
    channel_title: str | None
    embeddable: bool
    duration_s: int | None
    existing_media_id: str | None = Field(description="Set when this video is already in the library")


def _tax(v: list[str]) -> list[str]:
    if any(not TAXONOMY.match(t) for t in v):
        raise ValueError("taxonomy ids are lowercase dotted ids")
    return list(dict.fromkeys(v))


class VideoAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=300)
    source_id: str = Field(pattern=SOURCE_ID)
    title: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=1000)
    taxonomy_ids: list[str] = Field(default_factory=list, max_length=50)

    _t = field_validator("taxonomy_ids")(classmethod(lambda cls, v: _tax(v)))


class MediaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=1000)
    attribution: str | None = Field(default=None, max_length=500)
    taxonomy_ids: list[str] | None = Field(default=None, max_length=50)
    source_id: str | None = Field(default=None, pattern=SOURCE_ID)

    @field_validator("taxonomy_ids")
    @classmethod
    def _t(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _tax(v)


class MediaReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


class SectionMediaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_s: int | None = Field(default=None, ge=0)
    end_s: int | None = Field(default=None, ge=1)
    sort: int | None = Field(default=None, ge=0, le=32767)


class SectionMediaOut(BaseModel):
    section_id: str
    media_id: str
    sort: int | None
    start_s: int | None
    end_s: int | None


# ── helpers ──

MEDIA_SELECT = """
select m.id, m.kind, m.title, m.caption, m.source_id, m.review_status, m.url, m.youtube_id, m.channel_id,
       m.embeddable, m.duration_s, m.width, m.height, m.attribution, m.taxonomy_ids, m.metadata_fetched_at,
       coalesce((select array_agg(sm.section_id order by sm.section_id) from public.section_media sm
                  where sm.media_id = m.id), '{}') as section_ids
  from public.media_assets m
"""


async def media_previews(request: Request, found: list[Row]) -> dict[str, str | None]:
    resolver: MediaUrlResolver | None = getattr(request.app.state, "media_resolver", None)
    raws = [(r["id"], r["url"]) for r in found if r["url"] and r["kind"] != "video_youtube"]
    if resolver is None or not raws:
        return {}
    try:
        # Admin assets live in content-* buckets (never user-uploads), so the owner argument is never consulted.
        return await resolver.resolve(raws, uuid.UUID(int=0))
    except Exception as exc:  # a preview is a convenience: never fail the list over it  # noqa: BLE001
        log.warning("admin_media_preview_failed", error=type(exc).__name__)
        return {}


def media_out(r: Row, previews: dict[str, str | None]) -> AdminMedia:
    return AdminMedia(
        **{
            k: r[k]
            for k in (
                "id",
                "kind",
                "title",
                "caption",
                "source_id",
                "review_status",
                "youtube_id",
                "channel_id",
                "embeddable",
                "duration_s",
                "width",
                "height",
                "attribution",
                "metadata_fetched_at",
            )
        },
        preview_url=previews.get(r["id"]),
        taxonomy_ids=list(r["taxonomy_ids"] or []),
        section_ids=list(r["section_ids"] or []),
    )


async def load_media(conn: AsyncConnection[Any], media_id: str, *, lock: bool = False) -> Row:
    if lock:
        await conn.execute("select 1 from public.media_assets where id = %s for update", (media_id,))
    r = await one(conn, MEDIA_SELECT + " where m.id = %s", (media_id,))
    if r is None:
        raise not_found("media asset")
    return r


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}"


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def raster_to_webp(data: bytes) -> tuple[bytes, int, int]:
    """Decode a PNG/JPEG/WebP/GIF (first frame), drop metadata, bound the size, encode WebP.

    The pixel count is checked from the header before anything is decoded (Pillow's own bomb check only raises
    above twice its process-wide limit, which this code does not change), so a small file cannot expand into
    hundreds of MB on the Space; Pillow's DecompressionBombWarning is an error here too."""
    import warnings

    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ("PNG", "JPEG", "WEBP", "GIF"):
                    raise ValueError("unsupported image format")
                w, h = im.size
                if w <= 0 or h <= 0 or w * h > MAX_RASTER_PIXELS:
                    raise ValueError("image too large")
                im.seek(0)
                if max(w, h) > MAX_RASTER_SIDE:
                    # Let the decoder downscale (JPEG draft mode) before converting.
                    im.draft("RGB", (MAX_RASTER_SIDE, MAX_RASTER_SIDE))
                frame = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("not a readable image") from exc
    frame.thumbnail((MAX_RASTER_SIDE, MAX_RASTER_SIDE))
    out = io.BytesIO()
    frame.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue(), frame.width, frame.height


async def _body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"The file is larger than {limit // (1024 * 1024)} MB.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"The file is larger than {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(422, "The file is empty.")
    return data


def _buckets(request: Request) -> tuple[str, str]:
    from ..chapters_media import configured_buckets

    b = configured_buckets(request.app.state.settings)
    return b.content_public, b.content_private


# ── routes ──

UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    **ADMIN_ERRORS,
    **CONFLICT,
    **INVALID,
    413: {"model": ErrorResponse, "description": "The file is too large"},
    415: {"model": ErrorResponse, "description": "Not an SVG, PNG, JPEG, WebP, GIF or PDF"},
}


@router.get(
    "/media",
    operation_id="listAdminMedia",
    summary="Admin only: the media library (every review status)",
    response_model=AdminMediaList,
    responses=ADMIN_ERRORS,
)
async def list_media(
    request: Request,
    admin: ConsoleAdmin,
    kind: Annotated[MediaKind | None, Query()] = None,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 48,
    cursor: Annotated[str | None, Query(pattern=MEDIA_ID)] = None,
) -> AdminMediaList:
    async def run(conn: AsyncConnection[Any]) -> list[Row]:
        sql = MEDIA_SELECT + " where true"
        params: dict[str, Any] = {"limit": limit + 1}
        if kind:
            sql += " and m.kind = %(kind)s"
            params["kind"] = kind
        if review_status:
            sql += " and m.review_status = %(rs)s"
            params["rs"] = review_status
        if q and q.strip():
            sql += " and (m.id ilike %(q)s or m.title ilike %(q)s or m.caption ilike %(q)s)"
            params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if cursor:
            sql += " and m.id > %(cursor)s"
            params["cursor"] = cursor
        return await rows(conn, sql + " order by m.id limit %(limit)s", params)

    found = await run_tx(request, run)
    more = len(found) > limit
    found = found[:limit]
    previews = await media_previews(request, found)
    return AdminMediaList(
        items=[media_out(r, previews) for r in found], next_cursor=found[-1]["id"] if more and found else None
    )


@router.post(
    "/media/files",
    operation_id="uploadAdminMedia",
    summary="Admin only: upload a figure (SVG/PNG/JPEG/WebP/GIF) or a PDF as the raw request body",
    response_model=AdminMedia,
    status_code=201,
    responses=UPLOAD_ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                t: {"schema": {"type": "string", "format": "binary"}}
                for t in ("image/svg+xml", *sorted(RASTER_TYPES), "application/pdf")
            },
        }
    },
)
async def upload_media(
    request: Request,
    admin: ConsoleAdmin,
    source_id: Annotated[str, Query(pattern=SOURCE_ID)],
    title: Annotated[str, Query(min_length=1, max_length=300)],
    caption: Annotated[str | None, Query(max_length=1000)] = None,
    attribution: Annotated[str | None, Query(max_length=500)] = None,
    taxonomy_ids: Annotated[str | None, Query(max_length=2000, description="Comma-separated taxonomy ids")] = None,
) -> AdminMedia:
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ("image/svg+xml", "application/pdf", *RASTER_TYPES):
        raise HTTPException(415, "Upload an SVG, PNG, JPEG, WebP, GIF or PDF.")
    tax = _tax([t.strip() for t in (taxonomy_ids or "").split(",") if t.strip()])
    is_pdf = ctype == "application/pdf"
    kind: MediaKind = "pdf" if is_pdf else ("svg" if ctype == "image/svg+xml" else "image")
    clean_title = _clean(title)
    if clean_title is None:
        raise HTTPException(422, "The title must not be blank.")

    async def gate(conn: AsyncConnection[Any]) -> None:
        await check_asset(conn, source_id, use="host", asset=AssetFacts(kind))

    await run_tx(request, gate)  # refuse before reading or storing anything
    raw = await _body(request, MAX_PDF_BYTES if is_pdf else MAX_FIGURE_BYTES)
    width = height = None
    if is_pdf:
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(415, "This file is not a PDF.")
        data, store_type, ext = raw, "application/pdf", "pdf"
    elif kind == "svg":
        try:
            svg = sanitize_svg(raw, label=_clean(caption) or clean_title)
        except SvgError as exc:
            raise HTTPException(422, "This SVG cannot be used as a figure.") from exc
        data, width, height, store_type, ext = svg.data, svg.width, svg.height, "image/svg+xml", "svg"
    else:
        try:
            data, width, height = await asyncio.to_thread(raster_to_webp, raw)
        except ValueError as exc:
            raise HTTPException(415, "This image cannot be read.") from exc
        store_type, ext = "image/webp", "webp"
    store = object_store(request)
    if store is None:
        raise HTTPException(503, "File storage is not configured on this server.")
    _public, private = _buckets(request)
    mid = _new_id("pdf" if is_pdf else "fig")
    # Nothing is world-readable before an admin approves it: figures wait in the private bucket (review19 #7).
    bucket, folder = (private, PDF_FOLDER) if is_pdf else (private, STAGING_FOLDER)
    path = f"{folder}/{mid}.{ext}"
    try:
        await asyncio.to_thread(store.put, bucket, path, data, store_type)
    except Exception as exc:  # storage outage or refusal: nothing was recorded
        log.warning("admin_upload_failed", error=type(exc).__name__)
        raise HTTPException(503, "File storage cannot be reached right now. Try again in a moment.") from exc

    async def record(conn: AsyncConnection[Any]) -> Row:
        await check_asset(conn, source_id, use="host", asset=AssetFacts(kind))
        await conn.execute(
            "insert into public.media_assets (id, kind, source_id, title, caption, url, attribution, taxonomy_ids,"
            " review_status, width, height, content_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, 'unreviewed', %s, %s, %s)",
            (
                mid,
                kind,
                source_id,
                clean_title,
                _clean(caption),
                f"{bucket}/{path}",
                _clean(attribution),
                tax,
                width,
                height,
                hashlib.sha256(data).hexdigest(),
            ),
        )
        row = await load_media(conn, mid)
        await audit(conn, admin, "media.upload", "media", mid, after=row)
        return row

    row = await run_tx(request, record)
    return media_out(row, await media_previews(request, [row]))


@router.post(
    "/media/youtube/lookup",
    operation_id="lookupAdminYoutube",
    summary="Admin only: read a YouTube video's title, channel and embeddable flag (server-side)",
    response_model=VideoLookupOut,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **INVALID},
)
async def youtube_lookup(request: Request, admin: ConsoleAdmin, body: VideoLookupIn) -> VideoLookupOut:
    vid = youtube_id_of(body.url)
    if vid is None:
        raise HTTPException(422, "Paste a YouTube video link.")
    meta = await lookup_video(request, vid)
    if meta is None:
        raise not_found("YouTube video")

    async def run(conn: AsyncConnection[Any]) -> Row | None:
        return await one(conn, "select id from public.media_assets where youtube_id = %s order by id limit 1", (vid,))

    existing = await run_tx(request, run)
    return VideoLookupOut(**meta.__dict__, existing_media_id=existing["id"] if existing else None)


@router.post(
    "/media/youtube",
    operation_id="addAdminYoutube",
    summary="Admin only: add a YouTube video (embed only, through the official IFrame player)",
    response_model=AdminMedia,
    status_code=201,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def youtube_add(request: Request, admin: ConsoleAdmin, body: VideoAdd) -> AdminMedia:
    vid = youtube_id_of(body.url)
    if vid is None:
        raise HTTPException(422, "Paste a YouTube video link.")
    meta = await lookup_video(request, vid)
    if meta is None:
        raise not_found("YouTube video")

    async def run(conn: AsyncConnection[Any]) -> Row:
        if await one(conn, "select 1 from public.media_assets where youtube_id = %s", (vid,)):
            raise GateRefused("duplicate_video", vid)
        now = dt.datetime.now(dt.UTC)
        await check_asset(
            conn, body.source_id, use="embed", asset=AssetFacts("video_youtube", meta.embeddable, now), ref_id=vid
        )
        mid = f"yt-{vid.lower().replace('_', '-')}-{secrets.token_hex(2)}"
        await conn.execute(
            "insert into public.media_assets (id, kind, source_id, title, caption, youtube_id, channel_id, duration_s,"
            " embeddable, metadata_fetched_at, attribution, taxonomy_ids, review_status)"
            " values (%s, 'video_youtube', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'unreviewed')",
            (
                mid,
                body.source_id,
                _clean(body.title) or meta.title or None,
                _clean(body.caption),
                vid,
                meta.channel_id,
                meta.duration_s,
                meta.embeddable,
                now,
                meta.channel_title,
                body.taxonomy_ids,
            ),
        )
        row = await load_media(conn, mid)
        await audit(conn, admin, "media.youtube_add", "media", mid, after=row)
        return row

    return media_out(await run_tx(request, run), {})


@router.patch(
    "/media/{media_id}",
    operation_id="patchAdminMedia",
    summary="Admin only: edit a media asset's title, caption, attribution, taxonomy or source",
    response_model=AdminMedia,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def patch_media(
    request: Request, admin: ConsoleAdmin, media_id: Annotated[str, Path(pattern=MEDIA_ID)], body: MediaPatch
) -> AdminMedia:
    async def run(conn: AsyncConnection[Any]) -> Row:
        before = await load_media(conn, media_id, lock=True)
        updates: dict[str, Any] = {}
        for k in ("title", "caption", "attribution"):
            v = getattr(body, k)
            if v is not None:
                updates[k] = _clean(v)
        if body.taxonomy_ids is not None:
            updates["taxonomy_ids"] = body.taxonomy_ids
        if body.source_id is not None and body.source_id != before["source_id"]:
            # A new source is a new licence: the asset must pass its gates again before it is shown.
            facts = AssetFacts(
                before["kind"], before["embeddable"], before["metadata_fetched_at"], await media_licence(conn, media_id)
            )
            use: Use = "embed" if before["kind"] == "video_youtube" else "host"
            await check_asset(conn, body.source_id, use=use, asset=facts, ref_id=media_id)
            updates["source_id"] = body.source_id
            if before["review_status"] == "approved":
                await check_asset(conn, body.source_id, use="page", asset=facts, ref_id=media_id)
        if updates:
            await conn.execute(
                "update public.media_assets set " + ", ".join(f"{k} = %({k})s" for k in updates) + " where id = %(id)s",  # noqa: S608
                {**updates, "id": media_id},
            )
        after = await load_media(conn, media_id)
        await audit(conn, admin, "media.edit", "media", media_id, before=before, after=after)
        return after

    row = await run_tx(request, run)
    return media_out(row, await media_previews(request, [row]))


@router.post(
    "/media/{media_id}/review",
    operation_id="reviewAdminMedia",
    summary="Admin only: approve (gates apply) or reject a media asset",
    response_model=AdminMedia,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT},
)
async def review_media(
    request: Request, admin: ConsoleAdmin, media_id: Annotated[str, Path(pattern=MEDIA_ID)], body: MediaReview
) -> AdminMedia:
    sections: list[str] = []
    # Storage is not transactional, so a moving figure is written first, then the row, then the old copy is
    # removed (review19 R2): the database never points at a file that is gone, whatever fails or rolls back.
    current = await run_tx(request, lambda conn: load_media(conn, media_id))
    move = _plan_move(request, current, body.status)
    if move is not None:
        await _copy(request, move)

    async def run(conn: AsyncConnection[Any]) -> Row:
        before = await load_media(conn, media_id, lock=True)
        if move is not None and before["url"] != current["url"]:
            raise HTTPException(409, "This file changed while it was being reviewed. Reload and try again.")
        if body.status == "approved":
            review_use: Use = "embed" if before["kind"] == "video_youtube" else "host"
            await check_media_row(conn, media_id, use=review_use, require_approved=False)
            await check_media_row(conn, media_id, use="page", require_approved=False)
        elif before["section_ids"]:
            # Rejecting media a live section shows would leave a broken figure: refuse with the section.
            live = await rows(
                conn,
                "select s.id from public.chapter_sections s join public.chapters c on c.id = s.chapter_id"
                " where s.id = any(%s) and c.status = 'published' order by s.id limit 1",
                (list(before["section_ids"]),),
            )
            if live:
                raise GateRefused("media_in_use", live[0]["id"])
        await conn.execute(
            "update public.media_assets set review_status = %s, url = coalesce(%s, url) where id = %s",
            (body.status, move.url if move else None, media_id),
        )
        after = await load_media(conn, media_id)
        sections.extend(after["section_ids"])
        await audit(conn, admin, f"media.{body.status}", "media", media_id, before=before, after=after, note=body.note)
        return after

    try:
        row = await run_tx(request, run)
    except BaseException:
        if move is not None and not await _references(request, media_id, move.url):
            await _remove_quietly(request, move.dest)  # compensate: the new copy was never referenced
        raise
    if move is not None:  # committed: the old copy is no longer referenced
        await _remove_quietly(request, move.src)
    if sections:
        from ..embeddings.jobs import schedule_reindex

        schedule_reindex(request.app, ("sections",), ref_ids=sections)  # captions are part of section chunks
    return media_out(row, await media_previews(request, [row]))


@dataclass(frozen=True)
class _Move:
    src: tuple[str, str]
    dest: tuple[str, str]

    @property
    def url(self) -> str:
        return f"{self.dest[0]}/{self.dest[1]}"


def _plan_move(request: Request, row: Row, status: str) -> _Move | None:
    """Approve: a staged figure goes to the public bucket. Reject: a public admin figure goes back to the private
    staging folder, so a rejected file is never world-readable. None when nothing moves (PDFs and videos, seed
    figures, already in place)."""
    sp = parse_storage_path(row["url"]) if row["url"] else None
    if sp is None or row["kind"] not in ("svg", "image"):
        return None
    public, private = _buckets(request)
    name = sp.path.rsplit("/", 1)[-1]
    if status == "approved" and sp.bucket == private and sp.path.startswith(STAGING_FOLDER + "/"):
        return _Move((private, sp.path), (public, f"{FIGURE_FOLDER}/{name}"))
    if status == "rejected" and sp.bucket == public and sp.path.startswith(FIGURE_FOLDER + "/"):
        return _Move((public, sp.path), (private, f"{STAGING_FOLDER}/{name}"))
    return None


async def _copy(request: Request, move: _Move) -> None:
    store = object_store(request)
    if store is None:
        raise HTTPException(503, "File storage is not configured on this server.")
    name = move.dest[1].rsplit("/", 1)[-1]
    ctype = CONTENT_TYPES.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    try:
        data = await asyncio.to_thread(store.get, *move.src)
        await asyncio.to_thread(store.put, move.dest[0], move.dest[1], data, ctype)
    except Exception as exc:  # storage outage: nothing is recorded, the source is untouched
        log.warning("admin_media_move_failed", error=type(exc).__name__)
        raise HTTPException(503, "File storage cannot be reached right now. Try again in a moment.") from exc


async def _references(request: Request, media_id: str, url: str) -> bool:
    """Does the row point at `url` now? (A concurrent review of the same file may have committed the same move.)
    True when unsure, so a cleanup never removes a file that might be live: an orphan is the safe failure."""
    try:
        row = await run_tx(request, lambda conn: load_media(conn, media_id))
    except Exception:  # noqa: BLE001
        return True
    return bool(row["url"] == url)


async def _remove_quietly(request: Request, where: tuple[str, str]) -> None:
    """Best-effort removal of an unreferenced copy. A failure leaves an orphan object (logged), never a broken row."""
    store = object_store(request)
    if store is None:
        return
    try:
        await asyncio.to_thread(store.delete, *where)
    except Exception as exc:  # noqa: BLE001
        log.warning("admin_media_orphan_left", bucket=where[0], error=type(exc).__name__)


@router.put(
    "/sections/{section_id}/media/{media_id}",
    operation_id="attachAdminSectionMedia",
    summary="Admin only: attach a media asset to a section (videos: optional start/end seconds)",
    response_model=SectionMediaOut,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def attach_media(
    request: Request,
    admin: ConsoleAdmin,
    section_id: Annotated[str, Path(pattern=SECTION_ID)],
    media_id: Annotated[str, Path(pattern=MEDIA_ID)],
    body: SectionMediaIn,
) -> SectionMediaOut:
    if body.start_s is not None and body.end_s is not None and body.end_s <= body.start_s:
        raise HTTPException(422, "The end must come after the start.")

    async def run(conn: AsyncConnection[Any]) -> SectionMediaOut:
        sec = await one(
            conn,
            "select s.id from public.chapter_sections s join public.chapters c on c.id = s.chapter_id"
            " where s.id = %s and c.owner_user_id is null",
            (section_id,),
        )
        if sec is None:
            raise not_found("section")
        await check_media_row(conn, media_id, use="page", require_approved=True)
        before = await one(
            conn, "select * from public.section_media where section_id = %s and media_id = %s", (section_id, media_id)
        )
        r = await one(
            conn,
            "insert into public.section_media (section_id, media_id, sort, start_s, end_s) values (%s, %s, %s, %s, %s)"
            " on conflict (section_id, media_id) do update"
            " set sort = coalesce(excluded.sort, public.section_media.sort),"
            " start_s = excluded.start_s, end_s = excluded.end_s"
            " returning section_id, media_id, sort, start_s, end_s",
            (section_id, media_id, body.sort, body.start_s, body.end_s),
        )
        assert r is not None
        await audit(conn, admin, "section.media_attach", "section", section_id, before=before, after=r, note=media_id)
        return SectionMediaOut(**r)

    return await run_tx(request, run)


@router.delete(
    "/sections/{section_id}/media/{media_id}",
    operation_id="detachAdminSectionMedia",
    summary="Admin only: detach a media asset from a section",
    status_code=204,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT},
)
async def detach_media(
    request: Request,
    admin: ConsoleAdmin,
    section_id: Annotated[str, Path(pattern=SECTION_ID)],
    media_id: Annotated[str, Path(pattern=MEDIA_ID)],
) -> None:
    async def run(conn: AsyncConnection[Any]) -> None:
        from .content_schema import refs_of

        body_row = await one(conn, "select body from public.chapter_sections where id = %s", (section_id,))
        if body_row and any(mid == media_id for _k, mid in refs_of(body_row["body"])[0]):
            raise GateRefused("media_in_body", media_id)
        before = await one(
            conn, "select * from public.section_media where section_id = %s and media_id = %s", (section_id, media_id)
        )
        if before is None:
            raise not_found("attachment")
        await conn.execute(
            "delete from public.section_media where section_id = %s and media_id = %s", (section_id, media_id)
        )
        await audit(conn, admin, "section.media_detach", "section", section_id, before=before, note=media_id)

    await run_tx(request, run)
