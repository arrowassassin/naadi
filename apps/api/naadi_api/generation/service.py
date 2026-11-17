"""Generation orchestration: guards, quota, the Space call, storage and admin approval.

Everything the tutor tools and the HTTP routes share lives here, framework-free, so the tool adapter is thin and a
fake client / in-memory store drive the tests. Order of a run:

1. state: the token is configured, the feature is on, no ZeroGPU pause is active;
2. the prompt rules (no LLM) and the scrub; the stored prompt is the scrubbed one;
3. reserve a `generated_media` row under a per-user advisory lock (daily cap counted from this table, one run at a
   time per user), so concurrent confirms cannot overrun the cap;
4. the Space call with progress (queued → generating), then saving to `generated-media/{user_id}/{id}.webp|.mp4`;
5. the row becomes `ready`. A ZeroGPU quota error makes it `paused` with the reset time and sets a shared pause
   (newest unexpired `paused` row) — never retried, never routed to paid inference.

Never logged: prompts, file bytes, tokens.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row

from ..db import Database, DatabaseNotConfigured
from ..llm_config import GenerationSection
from ..logs import get_logger
from .approval import approve_generated
from .client import (
    MAX_VIDEO_SECONDS,
    GenerationClient,
    GenerationClientError,
    GenerationRequest,
    Kind,
    QuotaExceeded,
    next_utc_midnight,
)
from .common import COLUMNS, SOURCE_ID, GenerationError, row_to_admin_item, row_to_item
from .models import (
    AdminGeneratedItem,
    ApproveOut,
    GeneratedItem,
    GeneratedKind,
    GenerationState,
    GenerationStatusOut,
    UsageOut,
)
from .rules import check_prompt
from .store import MediaStore, MediaStoreError

log = get_logger("naadi.generation")

IMAGE_SIZE = (1024, 768)
VIDEO_SIZE = (768, 512)
STALE_RUN_MINUTES = 10
EXT = {"image": "webp", "video": "mp4"}

EventFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Preview:
    """What the confirm chip shows before anything is written (structured; the client words it)."""

    kind: Kind
    prompt: str
    seconds: int | None
    remaining_today: int
    daily_cap: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "prompt": self.prompt,
            "seconds": self.seconds,
            "remaining_today": self.remaining_today,
            "daily_cap": self.daily_cap,
        }


def utc_day_start(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


@asynccontextmanager
async def service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """The API's privileged role: clients cannot write `generated_media`; every statement filters on the caller."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute("set local statement_timeout = 10000")
                yield conn
        finally:
            conn.row_factory = tuple_row


class GenerationService:
    def __init__(
        self,
        *,
        db: Database,
        config: Callable[[], Awaitable[GenerationSection]],
        client: GenerationClient | None,
        store: MediaStore,
        buckets: tuple[str, str],  # (generated-media, content-public)
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._config = config
        self._client = client
        self._store = store
        self._private_bucket, self._public_bucket = buckets
        self._clock = clock

    # ── state ──

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def _paused_until(self, conn: psycopg.AsyncConnection[Any]) -> datetime | None:
        cur = await conn.execute(
            "select max(paused_until) as until from public.generated_media"
            " where status = 'paused' and paused_until > now()"
        )
        row = await cur.fetchone()
        return row["until"] if row else None

    async def _used(self, conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID) -> dict[str, int]:
        cur = await conn.execute(
            "select kind, count(*)::int as n from public.generated_media"
            " where user_id = %(u)s and status in ('queued','generating','ready') and created_at >= %(since)s"
            " group by kind",
            {"u": user_id, "since": utc_day_start(self._clock())},
        )
        out = {"image": 0, "video": 0}
        for r in await cur.fetchall():
            out[r["kind"]] = r["n"]
        return out

    async def status(self, user_id: uuid.UUID, *, enabled: bool = True) -> GenerationStatusOut:
        cfg = await self._config()
        async with service_tx(self._db) as conn:
            paused = await self._paused_until(conn)
            used = await self._used(conn, user_id)
        state: GenerationState
        if not self.configured:
            state = "not_configured"
        elif not enabled:
            state = "disabled"
        elif paused is not None:
            state = "paused"
        else:
            state = "ready"
        caps = {"image": cfg.image.max_per_user_per_day, "video": cfg.video.max_per_user_per_day}
        usage = {k: UsageOut(used=used[k], cap=caps[k], remaining=max(0, caps[k] - used[k])) for k in caps}
        return GenerationStatusOut(
            state=state,
            resets_at=paused if state == "paused" else next_utc_midnight(self._clock()),
            image=usage["image"],
            video=usage["video"],
            max_video_seconds=MAX_VIDEO_SECONDS,
            video_enabled=caps["video"] > 0,
        )

    # ── prepare (the confirm preview; writes nothing) ──

    async def prepare(self, user_id: uuid.UUID, kind: Kind, prompt: str, seconds: int | None = None) -> Preview:
        cfg = await self._config()
        self._require_available()
        cap = (cfg.image if kind == "image" else cfg.video).max_per_user_per_day
        if cap <= 0:
            raise GenerationError(403, "generation_kind_disabled")
        verdict = check_prompt(prompt)
        if not verdict.ok:
            raise GenerationError(422, verdict.code or "prompt_refused")
        secs: int | None = None
        if kind == "video":
            secs = max(
                1, min(int(seconds or MAX_VIDEO_SECONDS), MAX_VIDEO_SECONDS, cfg.video.max_seconds or MAX_VIDEO_SECONDS)
            )
        async with service_tx(self._db) as conn:
            paused = await self._paused_until(conn)
            if paused is not None:
                raise GenerationError(429, "generation_paused", resets_at=paused)
            used = (await self._used(conn, user_id))[kind]
        if used >= cap:
            raise GenerationError(429, "generation_cap_reached", resets_at=next_utc_midnight(self._clock()))
        return Preview(kind, verdict.scrubbed, secs, cap - used, cap)

    def _require_available(self) -> None:
        if not self.configured:
            raise GenerationError(503, "generation_not_configured")

    # ── run ──

    async def run(
        self,
        user_id: uuid.UUID,
        kind: Kind,
        prompt: str,
        seconds: int | None = None,
        on_event: EventFn | None = None,
    ) -> GeneratedItem:
        preview = await self.prepare(user_id, kind, prompt, seconds)
        cfg = await self._config()
        assert self._client is not None  # prepare() refused otherwise
        model = (cfg.image if kind == "image" else cfg.video).model
        width, height = IMAGE_SIZE if kind == "image" else VIDEO_SIZE
        media_id = await self._reserve(user_id, kind, preview.prompt, model, width, height, preview.seconds, cfg)

        async def emit(stage: str, rank: int | None = None) -> None:
            if on_event is not None:
                await on_event({"stage": stage, "media_id": str(media_id), "queue_position": rank})

        async def progress(stage: str, rank: int | None) -> None:
            if stage == "generating":
                await self._set_status(media_id, "generating")
            await emit(stage, rank)

        req = GenerationRequest(
            kind=kind,
            prompt=preview.prompt,
            seed=secrets.randbits(31),
            width=width,
            height=height,
            seconds=preview.seconds or MAX_VIDEO_SECONDS,
        )
        await emit("queued")
        try:
            result = await self._client.generate(req, progress)
        except QuotaExceeded as exc:
            await self._mark_paused(media_id, exc.resets_at)
            log.warning("generation_paused", kind=kind, resets_at=exc.resets_at.isoformat())
            raise GenerationError(429, "generation_paused", resets_at=exc.resets_at) from exc
        except GenerationClientError as exc:
            await self._mark_failed(media_id, exc.code)
            raise GenerationError(502, exc.code) from exc
        except Exception as exc:
            await self._mark_failed(media_id, "generation_failed")
            raise GenerationError(502, "generation_failed") from exc

        await emit("saving")
        path = self.storage_path(user_id, media_id, kind)
        try:
            await self._store.put(self._private_bucket, path, result.data, result.mime)
        except MediaStoreError as exc:
            await self._mark_failed(media_id, exc.code)
            raise GenerationError(502, exc.code) from exc
        async with service_tx(self._db) as conn:
            await conn.execute(
                "update public.generated_media set status = 'ready', storage_path = %(p)s, width = %(w)s,"
                " height = %(h)s, duration_s = %(d)s, model = %(m)s, error = null, paused_until = null"
                " where id = %(id)s and user_id = %(u)s",
                {
                    "p": path,
                    "w": result.width,
                    "h": result.height,
                    "d": result.duration_s,
                    "m": result.model,
                    "id": media_id,
                    "u": user_id,
                },
            )
        item = await self.get(user_id, media_id)
        assert item is not None
        return item

    @staticmethod
    def storage_path(user_id: uuid.UUID, media_id: uuid.UUID, kind: str) -> str:
        return f"{user_id}/{media_id}.{EXT[kind]}"

    async def _reserve(
        self,
        user_id: uuid.UUID,
        kind: Kind,
        prompt: str,
        model: str,
        width: int,
        height: int,
        seconds: int | None,
        cfg: GenerationSection,
    ) -> uuid.UUID:
        cap = (cfg.image if kind == "image" else cfg.video).max_per_user_per_day
        async with service_tx(self._db) as conn:
            await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 1502))", (f"gen:{user_id}",))
            # A run that never finished (process restart, lost job) stops blocking the user after a while.
            await conn.execute(
                "update public.generated_media set status = 'failed', error = 'generation_timeout'"
                " where user_id = %(u)s and status in ('queued','generating')"
                " and created_at < now() - make_interval(mins => %(m)s)",
                {"u": user_id, "m": STALE_RUN_MINUTES},
            )
            cur = await conn.execute(
                "select 1 from public.generated_media where user_id = %s and status in ('queued','generating') limit 1",
                (user_id,),
            )
            if await cur.fetchone():
                raise GenerationError(409, "generation_busy")
            if (await self._used(conn, user_id))[kind] >= cap:
                raise GenerationError(429, "generation_cap_reached", resets_at=next_utc_midnight(self._clock()))
            if (paused := await self._paused_until(conn)) is not None:
                raise GenerationError(429, "generation_paused", resets_at=paused)
            cur = await conn.execute(
                "insert into public.generated_media (user_id, kind, prompt, model, status, width, height, duration_s)"
                " values (%s, %s, %s, %s, 'queued', %s, %s, %s) returning id",
                (user_id, kind, prompt, model, width, height, float(seconds) if seconds else None),
            )
            row = await cur.fetchone()
        assert row is not None
        media_id: uuid.UUID = row["id"]
        return media_id

    async def _set_status(self, media_id: uuid.UUID, status: str) -> None:
        async with service_tx(self._db) as conn:
            await conn.execute(
                "update public.generated_media set status = %s where id = %s and status in ('queued','generating')",
                (status, media_id),
            )

    async def _mark_failed(self, media_id: uuid.UUID, code: str) -> None:
        async with service_tx(self._db) as conn:
            await conn.execute(
                "update public.generated_media set status = 'failed', error = %s where id = %s",
                (code[:120], media_id),
            )

    async def _mark_paused(self, media_id: uuid.UUID, until: datetime) -> None:
        async with service_tx(self._db) as conn:
            await conn.execute(
                "update public.generated_media set status = 'paused', error = 'generation_paused', paused_until = %s"
                " where id = %s",
                (until, media_id),
            )

    # ── reads ──

    async def _item(self, row: dict[str, Any], *, sign: bool = True) -> GeneratedItem:
        url: str | None = None
        if sign and row["status"] == "ready" and row["storage_path"]:
            try:
                url = await self._store.sign(self._private_bucket, row["storage_path"])
            except MediaStoreError:
                url = None
        return row_to_item(row, url)

    async def get(self, user_id: uuid.UUID, media_id: uuid.UUID) -> GeneratedItem | None:
        async with service_tx(self._db) as conn:
            cur = await conn.execute(
                f"select {COLUMNS} from public.generated_media where id = %s and user_id = %s",  # noqa: S608
                (media_id, user_id),
            )
            row = await cur.fetchone()
        return await self._item(row) if row else None

    async def list_items(
        self,
        user_id: uuid.UUID,
        *,
        kind: GeneratedKind | None,
        before: datetime | None,
        limit: int,
    ) -> tuple[list[GeneratedItem], datetime | None]:
        async with service_tx(self._db) as conn:
            cur = await conn.execute(
                f"select {COLUMNS} from public.generated_media"  # noqa: S608
                " where user_id = %(u)s and (%(k)s::text is null or kind = %(k)s)"
                " and (%(b)s::timestamptz is null or created_at < %(b)s)"
                " order by created_at desc limit %(n)s",
                {"u": user_id, "k": kind, "b": before, "n": limit + 1},
            )
            rows = await cur.fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        items = [await self._item(r) for r in rows]
        return items, (rows[-1]["created_at"] if more and rows else None)

    async def delete(self, user_id: uuid.UUID, media_id: uuid.UUID) -> bool:
        """Remove the row and its private file. A copy an admin published stays: it is a shared figure now."""
        async with service_tx(self._db) as conn:
            cur = await conn.execute(
                "delete from public.generated_media where id = %s and user_id = %s returning storage_path",
                (media_id, user_id),
            )
            row = await cur.fetchone()
        if row is None:
            return False
        if row["storage_path"]:
            try:
                await self._store.delete(self._private_bucket, [row["storage_path"]])
            except MediaStoreError:
                log.warning("generation_file_delete_failed")
        return True

    # ── admin ──

    async def list_pending(self, *, limit: int = 50) -> list[AdminGeneratedItem]:
        async with service_tx(self._db) as conn:
            cur = await conn.execute(
                f"select {COLUMNS} from public.generated_media"  # noqa: S608
                " where status = 'ready' and kind = 'image' and not approved_for_publish"
                " order by created_at desc limit %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [await self._admin_item(r) for r in rows]

    async def _admin_item(self, row: dict[str, Any]) -> AdminGeneratedItem:
        return row_to_admin_item(row, (await self._item(row)).url)

    async def approve(self, media_id: uuid.UUID, admin_id: uuid.UUID, section_id: str, caption: str) -> ApproveOut:
        """Publish a generated image as a shared chapter figure (admin only; the route is the gate)."""
        label = (await self._config()).label
        async with service_tx(self._db) as conn:
            return await approve_generated(
                conn,
                media_id,
                admin_id,
                section_id,
                caption,
                store=self._store,
                buckets=(self._private_bucket, self._public_bucket),
                label=label,
            )


__all__ = [
    "EXT",
    "IMAGE_SIZE",
    "SOURCE_ID",
    "VIDEO_SIZE",
    "GenerationError",
    "GenerationService",
    "Preview",
    "next_utc_midnight",
    "service_tx",
    "utc_day_start",
]
