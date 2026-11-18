"""Storage buckets and `storage.objects` policies.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16

Buckets (HANDOFF §7.5):
- `content-public`  figures, chapter images, posters, PDF thumbnails. Public read; service-role writes.
- `content-private` licensed / internal PDFs and source files. Signed URLs only; service-role writes.
- `user-uploads`    student files. Each user reads and writes only under `{auth.uid()}/…`.
- `vault`           passport, diploma, DS-2019 … Same per-user folder rule. Vault objects are never read by
                    any AI / ingest path; the backend enforces that in code (segments 10+), and no
                    service-role code path may list or download from this bucket except the owner's
                    explicit download request.
- `generated-media` AI-generated images / clips, per-user folder rule; publishing copies to content-public.

`storage.buckets` and `storage.objects` belong to the Supabase Storage service, which creates and migrates
them. This migration only inserts its bucket rows and adds policies; it never alters storage tables.
Size limits are the Supabase Free per-file maximum (50 MB) or lower.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MB = 1024 * 1024

IMAGES = ["image/png", "image/jpeg", "image/webp", "image/gif", "image/avif", "image/heic", "image/heif"]
DOCUMENTS = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
    "text/markdown",
    "text/html",
    "application/epub+zip",
    "application/rtf",
    "text/rtf",
]
AUDIO = ["audio/mpeg", "audio/mp4", "audio/aac", "audio/wav", "audio/x-wav", "audio/webm", "audio/ogg"]
VIDEO = ["video/mp4", "video/webm", "video/quicktime"]
# No `application/octet-stream`: it would let any file through. Measured File.type (Playwright, real paths):
# .zip → application/zip in Chromium, WebKit and Firefox (Windows Chrome/Edge send application/x-zip-compressed);
# Anki .apkg → application/vnd.anki in Firefox and EMPTY in Chromium and WebKit (iPad Safari), .colpkg → empty
# everywhere. The upload client (S15) therefore sends an explicit contentType: for an empty-type .apkg/.colpkg
# it checks the ZIP magic bytes and uses application/vnd.anki; the server re-sniffs every upload anyway.
ARCHIVES = ["application/zip", "application/x-zip-compressed", "application/vnd.anki"]

BUCKETS: dict[str, dict[str, object]] = {
    "content-public": {
        "public": True,
        "file_size_limit": 10 * MB,
        "allowed_mime_types": ["image/svg+xml", "image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"],
    },
    "content-private": {
        "public": False,
        "file_size_limit": 50 * MB,
        "allowed_mime_types": ["application/pdf", "application/epub+zip", *IMAGES, *AUDIO, *VIDEO],
    },
    "user-uploads": {
        "public": False,
        "file_size_limit": 50 * MB,
        "allowed_mime_types": [*DOCUMENTS, *IMAGES, *AUDIO, *VIDEO, *ARCHIVES],
    },
    "vault": {
        "public": False,
        "file_size_limit": 20 * MB,
        "allowed_mime_types": ["application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"],
    },
    "generated-media": {
        "public": False,
        "file_size_limit": 50 * MB,
        "allowed_mime_types": ["image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"],
    },
}

USER_FOLDER_BUCKETS = ("user-uploads", "vault", "generated-media")
POLICY_PREFIX = "naadi_"


def _array(values: Sequence[str]) -> str:
    return "array[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def _require_storage_schema() -> None:
    ok = (
        op.get_bind()
        .execute(
            text(
                "select to_regclass('storage.buckets') is not null and to_regclass('storage.objects') is not null"
                " and to_regprocedure('storage.foldername(text)') is not null"
            )
        )
        .scalar_one()
    )
    if not ok:
        raise RuntimeError(
            "storage.buckets / storage.objects are missing: the Supabase Storage service has not migrated this "
            "database (locally: apps/api/tests/db/stack.sh up)"
        )


def upgrade() -> None:
    _require_storage_schema()
    for bucket_id, cfg in BUCKETS.items():
        op.get_bind().execute(
            text(
                "insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)"
                " values (:id, :id, :public, :limit, :mimes)"
                " on conflict (id) do update set public = excluded.public,"
                " file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types"
            ),
            {
                "id": bucket_id,
                "public": cfg["public"],
                "limit": cfg["file_size_limit"],
                "mimes": cfg["allowed_mime_types"],
            },
        )

    own_folder = (
        f"bucket_id = any({_array(USER_FOLDER_BUCKETS)}) and (storage.foldername(name))[1] = (select auth.uid())::text"
    )
    p = POLICY_PREFIX
    op.execute(f"""
create policy {p}user_folders_select on storage.objects for select to authenticated using ({own_folder});
create policy {p}user_folders_insert on storage.objects for insert to authenticated with check ({own_folder});
create policy {p}user_folders_update on storage.objects for update to authenticated
  using ({own_folder}) with check ({own_folder});
create policy {p}user_folders_delete on storage.objects for delete to authenticated using ({own_folder});
-- content-public objects are served by the public URL without RLS; this lets clients list them too.
create policy {p}content_public_read on storage.objects for select to anon, authenticated
  using (bucket_id = 'content-public');
""")


def downgrade() -> None:
    p = POLICY_PREFIX
    for name in (
        "user_folders_select",
        "user_folders_insert",
        "user_folders_update",
        "user_folders_delete",
        "content_public_read",
    ):
        op.execute(f"drop policy if exists {p}{name} on storage.objects")
    bind = op.get_bind()
    ids = list(BUCKETS)
    in_use = bind.execute(
        text("select bucket_id, count(*) from storage.objects where bucket_id = any(:ids) group by 1"), {"ids": ids}
    ).all()
    if in_use:
        listing = ", ".join(f"{b} ({n} objects)" for b, n in in_use)
        raise RuntimeError(
            f"refusing to drop non-empty buckets: {listing}. Empty them through the Storage API first"
            " (deleting storage rows in SQL would orphan the stored files)."
        )
    # Storage guards direct deletes with a trigger; the buckets are verified empty above.
    bind.execute(text("select set_config('storage.allow_delete_query', 'true', true)"))
    bind.execute(text("delete from storage.buckets where id = any(:ids)"), {"ids": ids})
    bind.execute(text("select set_config('storage.allow_delete_query', 'false', true)"))
