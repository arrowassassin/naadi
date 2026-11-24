"""The retrieval index gains `video` rows: the Videos catalogue, so the assistant finds videos by hybrid search.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-24

- `embeddings.ref_type` allows 'video' (ref id = `video_catalog.media_id`). The row holds only Naadi's own catalogue
  metadata (title, topics, chapter, subject, step, channel name). No grant or policy changes: `embeddings` stays
  server-only, and every hit is re-read as the caller through `video_catalog`'s RLS (approved media only).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "'section','question','note','upload','pathway','url','ink','kb_doc'"


def _check(types: str) -> str:
    return (
        "alter table public.embeddings drop constraint embeddings_ref_type_check;"
        f" alter table public.embeddings add constraint embeddings_ref_type_check check (ref_type in ({types}));"
    )


def upgrade() -> None:
    op.execute(_check(OLD_TYPES + ",'video'"))


def downgrade() -> None:
    op.execute("delete from public.embeddings where ref_type = 'video'")
    op.execute(_check(OLD_TYPES))
