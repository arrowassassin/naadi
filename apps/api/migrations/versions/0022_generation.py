"""Image and video generation on the `naadi-gen` Space (segment 15b).

Revision ID: 0022
Revises: 0017
Create Date: 2026-11-17

- `generated_media` gains `status` ('queued' | 'generating' | 'ready' | 'failed' | 'paused'), `error` (a stable
  code, never provider text), `approved_by`, `approved_at` and `media_asset_id` (the shared `media_assets` row an
  admin approval created). Existing rows without a stored file become `failed`; the rest are `ready`.
- Shape checks: a `ready` row has a stored file; `approved_for_publish` needs an approver, a time and the shared
  asset; the prompt is 1-500 characters.
- Writes are the API's: `authenticated` keeps SELECT and DELETE on its own rows (the policies stay) and loses
  INSERT and UPDATE, so a client cannot create a row, rename it, mark it approved or set its status. The API
  counts the daily caps from this table, so a client-written row would be a way around them.
- `sources`: the `naadi-ai-generated` row (Naadi-owned output of an Apache-2.0 model; `allow_ai_ingest` false so
  generated images never enter RAG, embeddings or prompts). `config/sources.yaml` carries the same row for the
  seed importer; this insert keeps a database that has not been re-seeded valid for the approval flow.
- `paused_until`: when a ZeroGPU quota pause ends. The newest unexpired `paused` row is the pause every API process
  and the health panel agree on (no separate state table, nothing kept only in memory).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_ID = "naadi-ai-generated"

UPGRADE = f"""
alter table public.generated_media
  add column status text not null default 'ready'
    check (status in ('queued','generating','ready','failed','paused')),
  add column error text check (error is null or char_length(error) <= 120),
  add column approved_by uuid references auth.users(id) on delete set null,
  add column approved_at timestamptz,
  add column media_asset_id text references public.media_assets(id) on delete set null,
  add column paused_until timestamptz;
update public.generated_media set status = 'failed', error = 'legacy_no_file' where storage_path is null;
alter table public.generated_media alter column status set default 'queued';
alter table public.generated_media
  add constraint generated_media_ready_has_file check (status <> 'ready' or storage_path is not null),
  add constraint generated_media_prompt_len check (char_length(prompt) between 1 and 500),
  add constraint generated_media_pause_shape check (status <> 'paused' or paused_until is not null),
  add constraint generated_media_approval_shape check (
    not approved_for_publish or (approved_at is not null and media_asset_id is not null)
  );
create index generated_media_user_day_idx on public.generated_media (user_id, kind, created_at desc)
  where status in ('queued','generating','ready');
create index generated_media_pending_idx on public.generated_media (created_at desc)
  where status = 'ready' and not approved_for_publish;

revoke insert, update on table public.generated_media from authenticated;

insert into public.sources
  (id, name, url, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest,
   attribution_template, notes, verified, verified_url)
values (
  '{SOURCE_ID}', 'Naadi AI-generated illustrations', null, 'proprietary-naadi',
  true, true, true, false,
  'AI-generated illustration — not clinical reference',
  'Images and clips produced on the private naadi-gen Space (FLUX.1-schnell, Apache-2.0, and LTX-Video). '
  'Naadi-owned output. Never a clinical reference. Kept out of RAG, embeddings and prompts.',
  true, null
) on conflict (id) do nothing;
"""  # noqa: S608 - module constants only

DOWNGRADE_SOURCE = f"""
delete from public.media_assets where source_id = '{SOURCE_ID}';
delete from public.sources where id = '{SOURCE_ID}';
"""  # noqa: S608 - module constants only

DOWNGRADE = """
grant insert (id, user_id, kind, title, prompt, model, storage_path, width, height, duration_s)
  on table public.generated_media to authenticated;
grant update (title) on table public.generated_media to authenticated;

drop index if exists public.generated_media_pending_idx;
drop index if exists public.generated_media_user_day_idx;
alter table public.generated_media
  drop constraint if exists generated_media_approval_shape,
  drop constraint if exists generated_media_pause_shape,
  drop constraint if exists generated_media_prompt_len,
  drop constraint if exists generated_media_ready_has_file,
  drop column if exists paused_until,
  drop column if exists media_asset_id,
  drop column if exists approved_at,
  drop column if exists approved_by,
  drop column if exists error,
  drop column if exists status;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_SOURCE)
    op.execute(DOWNGRADE)
