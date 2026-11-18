"""The `generated_media` approval queue (segment 19; moves segment 15b's admin approve into the registry).

A student's AI-generated image never becomes shared content by itself (videos never do; segment 15b). Approving
one (params `section_id`, `caption`) copies the file out of the private `generated-media` bucket into
`content-public`, creates an approved shared `media_assets` row whose source is `naadi-ai-generated` and whose
caption ends with the AI label (config/models.yaml `generation.label`), and publishes a new revision of the section
with the figure appended (a valid `figure` node: the caption lives on the media row). Rejecting marks it decided
without publishing. The licence gates run as for any upload.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import secrets
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from ..chapters_media import configured_buckets, parse_storage_path
from .chapters import gate_body, publish_body, validate_body
from .common import one, rows
from .licence import AssetFacts, GateRefused, check_asset
from .media import object_store_of
from .queues import ItemGone, QueueContext, QueueDecision, QueueItem, QueuePage, QueueRef, register_queue

AI_SOURCE = "naadi-ai-generated"
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


@lru_cache(maxsize=4)
def ai_label(config_dir: Path) -> str:
    raw = yaml.safe_load((config_dir / "models.yaml").read_text(encoding="utf-8")) or {}
    label = str(((raw.get("generation") or {}).get("label")) or "").strip()
    if not label:
        raise RuntimeError("config/models.yaml generation.label is not set")
    return label


async def _columns(conn: AsyncConnection[Any]) -> set[str]:
    found = await rows(
        conn,
        "select column_name from information_schema.columns"
        " where table_schema = 'public' and table_name = 'generated_media'",
    )
    return {r["column_name"] for r in found}


async def _pending_sql(conn: AsyncConnection[Any]) -> str:
    cols = await _columns(conn)
    # Segment 15b adds status/approved_at: only finished items wait for a decision; a decided item has approved_at
    # (approved) or approved_by set with approved_for_publish false (rejected).
    # Only images are publishable (segment 15b: a generated video never becomes shared content).
    sql = "g.approved_for_publish = false and g.storage_path is not null and g.kind = 'image'"
    if "status" in cols:
        sql += " and g.status = 'ready'"
    if "approved_by" in cols:
        sql += " and g.approved_by is null"
    return sql


async def _list(ctx: QueueContext, *, limit: int, cursor: str | None) -> QueuePage:
    where = await _pending_sql(ctx.conn)
    params: dict[str, Any] = {"limit": limit + 1}
    sql = (
        "select g.id, g.kind, g.title, g.prompt, g.width, g.height, g.duration_s, g.created_at, g.storage_path"  # noqa: S608 - constant where
        f" from public.generated_media g where {where}"
    )
    if cursor:
        sql += " and (g.created_at, g.id) > (select created_at, id from public.generated_media where id::text = %(c)s)"
        params["c"] = cursor
    found = await rows(ctx.conn, sql + " order by g.created_at, g.id limit %(limit)s", params)
    total = await one(ctx.conn, f"select count(*)::int as n from public.generated_media g where {where}")  # noqa: S608
    more = len(found) > limit
    found = found[:limit]
    previews = await _sign(ctx, [str(r["storage_path"]) for r in found])
    items = [
        QueueItem(
            id=str(r["id"]),
            kind="generated_media",
            created_at=r["created_at"],
            title=r["title"],
            ref=QueueRef(type="generated_media", id=str(r["id"])),
            detail={
                "media_kind": r["kind"],
                "prompt": r["prompt"],
                "width": r["width"],
                "height": r["height"],
                "duration_s": float(r["duration_s"]) if r["duration_s"] is not None else None,
                "ai_generated": True,
                "preview_url": previews.get(str(r["storage_path"])),
            },
        )
        for r in found
    ]
    return QueuePage(
        items=items, next_cursor=str(found[-1]["id"]) if more and found else None, total=int(total["n"]) if total else 0
    )


async def _sign(ctx: QueueContext, paths: list[str]) -> dict[str, str | None]:
    """Short-lived signed URLs for the admin preview (segment 15b's store; `admin_generated_signer` in tests).
    A preview is a convenience: a signing failure shows the item without one."""
    signer = getattr(ctx.app.state, "admin_generated_signer", None)
    bucket = configured_buckets(ctx.app.state.settings).generated_media
    if signer is None:
        settings = ctx.app.state.settings
        if not settings.supabase_url or settings.supabase_service_role_key is None:
            return {}
        from ..generation.store import SupabaseMediaStore

        signer = SupabaseMediaStore(settings).sign
    out: dict[str, str | None] = {}
    for p in paths:
        try:
            out[p] = await signer(bucket, p.removeprefix(f"{bucket}/"))
        except Exception:  # noqa: BLE001 - see docstring
            out[p] = None
    return out


def _uuid(item_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(item_id)
    except ValueError as exc:
        raise ItemGone() from exc


async def _load_pending(conn: AsyncConnection[Any], item_id: str) -> dict[str, Any]:
    where = await _pending_sql(conn)
    r = await one(
        conn,
        f"select g.* from public.generated_media g where g.id = %s and {where} for update",  # noqa: S608 - constant where
        (_uuid(item_id),),
    )
    if r is None:
        raise ItemGone()
    return r


def _param_str(params: dict[str, JsonValue], key: str, max_len: int) -> str:
    v = params.get(key)
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        raise GateRefused(f"missing_{key}")
    return v.strip()


async def _approve(ctx: QueueContext, item_id: str, note: str | None, params: dict[str, JsonValue]) -> QueueDecision:
    conn = ctx.conn
    section_id = _param_str(params, "section_id", 128)
    caption = _param_str(params, "caption", 800)
    g = await _load_pending(conn, item_id)
    media_kind = "image"
    await check_asset(conn, AI_SOURCE, use="host", asset=AssetFacts(media_kind), ref_id=item_id)
    await check_asset(conn, AI_SOURCE, use="page", asset=AssetFacts(media_kind), ref_id=item_id)
    section = await one(
        conn,
        "select s.id, s.body from public.chapter_sections s join public.chapters c on c.id = s.chapter_id"
        " where s.id = %s and c.owner_user_id is null for update of s",
        (section_id,),
    )
    if section is None:
        raise GateRefused("section_missing", section_id)
    settings = ctx.app.state.settings
    buckets = configured_buckets(settings)
    # 15b stores the object path inside the generated-media bucket (`<user id>/<file>`).
    src_bucket, src_path = buckets.generated_media, str(g["storage_path"]).removeprefix(f"{buckets.generated_media}/")
    if parse_storage_path(f"{src_bucket}/{src_path}") is None:
        raise GateRefused("bad_storage_path", item_id)
    ext = src_path.rsplit(".", 1)[-1].lower()
    types = IMAGE_TYPES
    if ext not in types:
        raise GateRefused("bad_storage_path", item_id)
    store = object_store_of(ctx.app)
    if store is None:
        raise GateRefused("storage_unavailable", item_id)
    data = await asyncio.to_thread(store.get, src_bucket, src_path)
    mid = f"gen-{secrets.token_hex(5)}"
    dest_bucket = buckets.content_public
    dest_path = f"figures/generated/{mid}.{ext}"
    await asyncio.to_thread(store.put, dest_bucket, dest_path, data, types[ext])
    label = ai_label(settings.config_dir)
    full_caption = caption if caption.endswith(label) else f"{caption} — {label}"
    await conn.execute(
        "insert into public.media_assets (id, kind, source_id, title, caption, url, attribution, review_status, width,"
        " height, duration_s, content_sha256) values (%s, %s, %s, %s, %s, %s, %s, 'approved', %s, %s, %s, %s)",
        (
            mid,
            media_kind,
            AI_SOURCE,
            g["title"] or caption[:120],
            full_caption,
            f"{dest_bucket}/{dest_path}",
            label,
            g["width"],
            g["height"],
            int(g["duration_s"]) if g["duration_s"] is not None else None,
            hashlib.sha256(data).hexdigest(),
        ),
    )
    body = copy.deepcopy(section["body"]) if isinstance(section["body"], dict) else {"type": "doc", "content": []}
    node: dict[str, Any] = {"type": "figure", "attrs": {"mediaId": mid}}
    content = body.get("content")
    if not isinstance(content, list):
        content = []
        body["content"] = content
    content.append(node)
    validate_body(body)
    await gate_body(conn, body)
    live = await one(
        conn,
        "select id from public.chapter_section_revisions where section_id = %s and status = 'published'"
        " and origin <> 'seed' order by published_at desc, id desc limit 1",
        (section_id,),
    )
    await conn.execute(
        "insert into public.chapter_section_revisions (section_id, body, author_user_id, origin, note, status,"
        " parent_revision_id, published_at, published_by) values (%s, %s, %s, 'admin', %s, 'published', %s, now(), %s)",
        (section_id, Jsonb(body), ctx.admin.user_id, note, live["id"] if live else None, ctx.admin.user_id),
    )
    await publish_body(conn, ctx.admin, section_id, body)
    cols = await _columns(conn)
    sets = ["approved_for_publish = true"]
    if "approved_by" in cols:
        sets.append("approved_by = %(admin)s")
    if "approved_at" in cols:
        sets.append("approved_at = now()")
    if "media_asset_id" in cols:
        sets.append("media_asset_id = %(mid)s")
    await conn.execute(
        "update public.generated_media set " + ", ".join(sets) + " where id = %(id)s",  # noqa: S608 - constant fragments
        {"admin": ctx.admin.user_id, "mid": mid, "id": g["id"]},
    )
    from ..embeddings.jobs import schedule_reindex

    schedule_reindex(ctx.app, ("sections",), ref_ids=[section_id])
    return QueueDecision(
        id=item_id,
        status="approved",
        before={"generated_media": str(g["id"]), "section_body": section["body"]},
        after={"media_id": mid, "section_id": section_id, "section_body": body},
    )


async def _reject(ctx: QueueContext, item_id: str, note: str | None) -> QueueDecision:
    g = await _load_pending(ctx.conn, item_id)
    cols = await _columns(ctx.conn)
    if "approved_by" not in cols:
        # Before 15b's columns there is nowhere to record a rejection; refuse rather than pretend.
        raise GateRefused("reject_unsupported", item_id)
    await ctx.conn.execute(
        "update public.generated_media set approved_by = %s"  # noqa: S608 - constant where fragments
        + (", approved_at = null" if "approved_at" in cols else "")
        + " where id = %s",
        (ctx.admin.user_id, g["id"]),
    )
    return QueueDecision(
        id=item_id,
        status="rejected",
        before={"generated_media": str(g["id"])},
        after={"rejected_by": str(ctx.admin.user_id)},
    )


register_queue("generated_media", _list, _approve, _reject, ref_type="generated_media")
