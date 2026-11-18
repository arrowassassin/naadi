"""Notes, highlights, notebook pages and handwriting OCR (segment 13).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-17

- `notes` gains `kind` ('note' | 'highlight' | 'page'), `title`, `sort` (page order within a notebook), `paper` (a
  page's paper override, same set as `notebooks.paper`), `canvas` ('paper' | 'whiteboard': a whiteboard page keeps
  its Excalidraw scene in `body`) and `body_text` (plain text for search, computed by the API from `body`). Shape
  checks: a highlight is anchored to a section or a question with a position and one of the three colours, and is
  never in a notebook; only highlights carry a colour; an anchor other than `free` names its target. A section has
  at most one `kind 'note'` row per student (the Reader's page-notes panel): a partial unique index.
- `notebooks` gains `updated_at` (touched by the shared trigger), a title length check and `cover` restricted to
  six colour token names of packages/ui/src/tokens.css (`--color-<name>`).
- `ink_layers` gains `ocr_status` ('pending' | 'done' | 'deferred' | 'failed'), `ocr_hash` (the layer's
  `content_hash` the recognised text was made from) and `ocr_at`, written by the API's save path (queueing) and
  the `ink_ocr` graph only.
- Writes: `notes` and `notebooks` become API-written, like ink in 0009: the API computes `body_text`, keeps the
  embeddings in step, refuses question anchors before a graded attempt and checks that a section anchor is
  visible, none of which RLS can do. `authenticated` keeps SELECT on its own rows (RLS) and loses INSERT, UPDATE
  and DELETE; the four `_own` policies stay (unreachable for the revoked commands, they document the row filter).
  `ink_layers` is already select-only for clients (0009), so the OCR columns are backend-only.
- `agent_health.kind` gains `ink_ocr` (one row per OCR pass that did work, counts only).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Colour token names (packages/ui/src/tokens.css `--color-<name>`) a notebook cover may use.
COVERS = ("lime", "violet", "amber", "paper", "surface-2", "ink-violet")
PAPERS = ("ruled", "dotted", "grid", "cornell", "blank")


def _in(values: Sequence[str]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


UPGRADE = f"""
-- notebooks
update public.notebooks set cover = null where cover is not null and cover not in {_in(COVERS)};
update public.notebooks set title = left(btrim(title), 200) where title <> left(btrim(title), 200);
update public.notebooks set title = 'Notebook' where title = '';
alter table public.notebooks
  add column updated_at timestamptz not null default now(),
  add constraint notebooks_cover_check check (cover is null or cover in {_in(COVERS)}),
  add constraint notebooks_title_check check (char_length(title) between 1 and 200);
create trigger notebooks_touch_updated_at before update on public.notebooks
  for each row execute function public.naadi_touch_updated_at();

-- notes
alter table public.notes
  add column kind text not null default 'note' check (kind in ('note','highlight','page')),
  add column title text check (title is null or char_length(title) <= 200),
  add column sort int not null default 0 check (sort between 0 and 100000),
  add column paper text check (paper is null or paper in {_in(PAPERS)}),
  add column canvas text not null default 'paper' check (canvas in ('paper','whiteboard')),
  add column body_text text,
  add constraint notes_anchor_shape check ((coalesce(anchor_type, 'free') = 'free') = (anchor_id is null)),
  add constraint notes_anchor_pos_object check (anchor_pos is null or jsonb_typeof(anchor_pos) = 'object'),
  add constraint notes_body_object check (body is null or jsonb_typeof(body) = 'object'),
  add constraint notes_color_on_highlights check (highlight_color is null or kind = 'highlight'),
  add constraint notes_highlight_shape check (
    kind <> 'highlight' or (
      anchor_type in ('section','question') and anchor_id is not null and anchor_pos is not null
      and highlight_color is not null and notebook_id is null and canvas = 'paper'
    )
  );
create unique index notes_one_section_note on public.notes (user_id, anchor_id)
  where kind = 'note' and anchor_type = 'section';
create index notes_user_kind_updated_idx on public.notes (user_id, kind, updated_at desc);
create index notes_notebook_sort_idx on public.notes (notebook_id, sort) where notebook_id is not null;

-- ink_layers: OCR state (written by the API save path and the ink_ocr graph)
alter table public.ink_layers
  add column ocr_status text check (ocr_status in ('pending','done','deferred','failed')),
  add column ocr_hash text,
  add column ocr_at timestamptz;
create index ink_layers_ocr_queue_idx on public.ink_layers (ocr_status, updated_at)
  where ocr_status in ('pending','deferred');

-- API-only writes for notes and notebooks (select stays, under the own-row policy)
revoke insert, update, delete on table public.notes from authenticated;
revoke insert, update, delete on table public.notebooks from authenticated;

alter table public.agent_health drop constraint agent_health_kind_check,
  add constraint agent_health_kind_check check (kind in ('ping','tavily_usage','coach','ink_ocr'));
"""  # noqa: S608 - module constants only

DOWNGRADE_HEALTH = """
delete from public.agent_health where kind = 'ink_ocr';
alter table public.agent_health drop constraint agent_health_kind_check,
  add constraint agent_health_kind_check check (kind in ('ping','tavily_usage','coach'));
"""

DOWNGRADE_GRANTS = """
grant insert, update, delete on table public.notes to authenticated;
grant insert, update, delete on table public.notebooks to authenticated;
"""

DOWNGRADE = """
drop index if exists public.ink_layers_ocr_queue_idx;
alter table public.ink_layers
  drop column if exists ocr_at,
  drop column if exists ocr_hash,
  drop column if exists ocr_status;

drop index if exists public.notes_notebook_sort_idx;
drop index if exists public.notes_user_kind_updated_idx;
drop index if exists public.notes_one_section_note;
alter table public.notes
  drop constraint if exists notes_highlight_shape,
  drop constraint if exists notes_color_on_highlights,
  drop constraint if exists notes_body_object,
  drop constraint if exists notes_anchor_pos_object,
  drop constraint if exists notes_anchor_shape,
  drop column if exists body_text,
  drop column if exists canvas,
  drop column if exists paper,
  drop column if exists sort,
  drop column if exists title,
  drop column if exists kind;

drop trigger if exists notebooks_touch_updated_at on public.notebooks;
alter table public.notebooks
  drop constraint if exists notebooks_title_check,
  drop constraint if exists notebooks_cover_check,
  drop column if exists updated_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_HEALTH)
    op.execute(DOWNGRADE_GRANTS)
    op.execute(DOWNGRADE)
