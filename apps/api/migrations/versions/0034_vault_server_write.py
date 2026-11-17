"""The vault bucket is written by the API only (segment 17 review).

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-17

0002 gave `authenticated` insert, update and delete on `storage.objects` under `{uid}/` for every user-folder bucket,
the vault included. A student could therefore write a vault object without the API: skip the byte sniff, the size and
SHA-256 record and the document cap, overwrite a checked document with other bytes, or delete an object and leave its
row behind. Uploads already go through a signed upload token (the API signs it after creating the row), which needs no
client policy, and every other vault write is the API's with the service role.

This migration recreates the three write policies without `vault`. Select stays (a student may still read their own
folder; the API's signed URL is the way the app opens a file). `user-uploads` and `generated-media` are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _own(buckets: Sequence[str]) -> str:
    arr = "array[" + ", ".join(f"'{b}'" for b in buckets) + "]"
    return f"bucket_id = any({arr}) and (storage.foldername(name))[1] = (select auth.uid())::text"


def _drop() -> str:
    return """
drop policy if exists naadi_user_folders_insert on storage.objects;
drop policy if exists naadi_user_folders_update on storage.objects;
drop policy if exists naadi_user_folders_delete on storage.objects;
"""


def _create(buckets: Sequence[str]) -> str:
    own = _own(buckets)
    return f"""
create policy naadi_user_folders_insert on storage.objects for insert to authenticated with check ({own});
create policy naadi_user_folders_update on storage.objects for update to authenticated
  using ({own}) with check ({own});
create policy naadi_user_folders_delete on storage.objects for delete to authenticated using ({own});
"""


def upgrade() -> None:
    op.execute(_drop())
    op.execute(_create(("user-uploads", "generated-media")))


def downgrade() -> None:
    op.execute(_drop())
    op.execute(_create(("user-uploads", "vault", "generated-media")))
