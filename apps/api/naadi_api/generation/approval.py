"""Admin approval: publish a generated image as a shared chapter figure.

`approve_generated` is the single implementation. The admin route and the approval queue (S19) both call it with an
open service-role transaction. It copies the file to `content-public/generated/`, creates the shared `media_assets`
row (source `naadi-ai-generated`, caption ending in the AI label), appends a figure to the section, records a
`chapter_section_revisions` row (origin `admin`) and marks the item approved. Idempotent: an approved item returns
its existing figure. Videos and student-owned chapters are never published.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import psycopg

from ..logs import get_logger
from .common import COLUMNS, SOURCE_ID, GenerationError, row_to_admin_item
from .models import GeneratedApproveOut
from .store import MediaStore, MediaStoreError

log = get_logger("naadi.generation")

_SELECT_FOR_UPDATE = f"select {COLUMNS} from public.generated_media where id = %s for update"  # noqa: S608
_APPROVE = (
    "update public.generated_media set approved_for_publish = true, approved_by = %s, approved_at = now(),"  # noqa: S608
    f" media_asset_id = %s where id = %s returning {COLUMNS}"
)


def default_label() -> str:
    """The AI label from `config/models.yaml` (`generation.label`), for callers that have no service."""
    import yaml

    from ..settings import get_settings

    raw = yaml.safe_load((get_settings().config_dir / "models.yaml").read_text(encoding="utf-8"))
    return str(raw["generation"]["label"])


async def approve_generated(
    conn: psycopg.AsyncConnection[Any],
    item_id: uuid.UUID,
    admin_id: uuid.UUID,
    section_id: str,
    caption: str,
    *,
    store: MediaStore | None = None,
    buckets: tuple[str, str] | None = None,
    label: str | None = None,
) -> GeneratedApproveOut:
    """Approve one item (see the module docstring). `conn` is a service-role transaction with dict rows.

    `store`, `buckets` (generated-media, content-public) and `label` default to the process settings and config, so
    S19's queue can call `approve_generated(conn, item_id, admin_id, section_id, caption)` as is.
    """
    if store is None or buckets is None:
        from ..chapters_media import configured_buckets
        from ..settings import get_settings
        from .store import SupabaseMediaStore

        settings = get_settings()
        store = store or SupabaseMediaStore(settings)
        b = configured_buckets(settings)
        buckets = buckets or (b.generated_media, b.content_public)
    private_bucket, public_bucket = buckets
    label = label or default_label()

    cur = await conn.execute(_SELECT_FOR_UPDATE, (item_id,))
    row = await cur.fetchone()
    if row is None:
        raise GenerationError(404, "generated_not_found")
    if row["approved_for_publish"]:
        cur = await conn.execute(
            "select section_id from public.section_media where media_id = %s limit 1", (row["media_asset_id"],)
        )
        existing = await cur.fetchone()
        return GeneratedApproveOut(
            item=row_to_admin_item(row),
            media_id=row["media_asset_id"],
            section_id=existing["section_id"] if existing else section_id,
        )
    if row["status"] != "ready" or not row["storage_path"]:
        raise GenerationError(409, "generated_not_ready")
    if row["kind"] != "image":
        raise GenerationError(422, "generated_video_not_publishable")
    cur = await conn.execute(
        "select s.id, s.body, s.body_html from public.chapter_sections s"
        " join public.chapters c on c.id = s.chapter_id"
        " where s.id = %s and c.owner_user_id is null for update of s",
        (section_id,),
    )
    section = await cur.fetchone()
    if section is None:
        raise GenerationError(404, "section_not_found")
    try:
        data = await store.get(private_bucket, row["storage_path"])
    except MediaStoreError as exc:
        raise GenerationError(502, exc.code) from exc

    shared_id = f"gen-{uuid.uuid4().hex[:16]}"
    public_path = f"generated/{shared_id}.webp"
    try:
        await store.put(public_bucket, public_path, data, "image/webp")
    except MediaStoreError as exc:
        raise GenerationError(502, exc.code) from exc
    try:
        full_caption = f"{caption.strip()} — {label}"
        await conn.execute(
            "insert into public.media_assets (id, kind, source_id, title, caption, url, attribution, width, height,"
            " review_status, content_sha256) values (%s, 'image', %s, %s, %s, %s, %s, %s, %s, 'approved', %s)",
            (
                shared_id,
                SOURCE_ID,
                (row["title"] or row["prompt"])[:200],
                full_caption,
                f"{public_bucket}/{public_path}",
                label,
                row["width"],
                row["height"],
                hashlib.sha256(data).hexdigest(),
            ),
        )
        body = section["body"] if isinstance(section["body"], dict) else {"type": "doc", "content": []}
        content = [
            *(body.get("content") or []),
            {"type": "figure", "attrs": {"mediaId": shared_id, "caption": full_caption}},
        ]
        new_body = {**body, "type": "doc", "content": content}
        html = (section["body_html"] or "") + f'\n<figure data-media-id="{shared_id}"></figure>'
        await conn.execute(
            "insert into public.chapter_section_revisions (section_id, body, body_html, author_user_id, origin, note)"
            " values (%s, %s::jsonb, %s, %s, 'admin', 'generated_media_approved')",
            (section_id, json.dumps(new_body), html, admin_id),
        )
        await conn.execute(
            "update public.chapter_sections set body = %s::jsonb, body_html = %s where id = %s",
            (json.dumps(new_body), html, section_id),
        )
        await conn.execute(
            "insert into public.section_media (section_id, media_id, sort)"
            " select %(s)s, %(m)s, coalesce(max(sort) + 1, 0) from public.section_media where section_id = %(s)s",
            {"s": section_id, "m": shared_id},
        )
        cur = await conn.execute(_APPROVE, (admin_id, shared_id, item_id))
        updated = await cur.fetchone()
    except Exception:
        # The caller's transaction rolls back; do not leave an orphan public copy behind.
        try:
            await store.delete(public_bucket, [public_path])
        except MediaStoreError:
            log.warning("generation_cleanup_failed")
        raise
    assert updated is not None
    return GeneratedApproveOut(item=row_to_admin_item(updated), media_id=shared_id, section_id=section_id)


__all__ = ["approve_generated", "default_label"]
