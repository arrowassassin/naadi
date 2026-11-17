"""Review fixes of segment 20: card sources and the own-card write check.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-17

- `flashcards.source_id` (references `sources`): every text asset names its source (CLAUDE.md content rule). Seed
  cards are `naadi-original-v1`, imported Anki cards are `user-upload` (a check keeps that true), and a student's own
  or the assistant's cards have none (their own words).
- The student's own-card insert/update policies now also require `anki_guid`, `anki_note_type` and `source_id` to be
  null. Those columns belong to the import API (privileged role): a student could otherwise stamp an Anki id on a
  typed card through the Data API and turn a later import of that note into a silent no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"owner_user_id = {UID}"
CHECK = (
    f"{OWN} and origin = 'user' and anki_guid is null and anki_note_type is null and source_id is null"  # noqa: S608
    " and (question_id is null or exists (select 1 from public.questions p where p.id = flashcards.question_id))"
    " and (section_id is null or exists (select 1 from public.chapter_sections p where p.id = flashcards.section_id))"
)
OLD_CHECK = (
    f"{OWN} and origin = 'user'"  # noqa: S608
    " and (question_id is null or exists (select 1 from public.questions p where p.id = flashcards.question_id))"
    " and (section_id is null or exists (select 1 from public.chapter_sections p where p.id = flashcards.section_id))"
)

UPGRADE = f"""
alter table public.flashcards add column source_id text references public.sources(id);
update public.flashcards set source_id = 'naadi-original-v1' where origin = 'seed' and source_id is null
  and exists (select 1 from public.sources s where s.id = 'naadi-original-v1');
update public.flashcards set source_id = 'user-upload' where origin = 'anki_import';
alter table public.flashcards add constraint flashcards_anki_source_check
  check (origin <> 'anki_import' or source_id = 'user-upload');

drop policy flashcards_insert_own on public.flashcards;
drop policy flashcards_update_own on public.flashcards;
create policy flashcards_insert_own on public.flashcards for insert to authenticated with check ({CHECK});
create policy flashcards_update_own on public.flashcards for update to authenticated
  using ({OWN} and origin = 'user') with check ({CHECK});
"""  # noqa: S608 - module constants only

DOWNGRADE_POLICIES = f"""
drop policy flashcards_insert_own on public.flashcards;
drop policy flashcards_update_own on public.flashcards;
create policy flashcards_insert_own on public.flashcards for insert to authenticated with check ({OLD_CHECK});
create policy flashcards_update_own on public.flashcards for update to authenticated
  using ({OWN} and origin = 'user') with check ({OLD_CHECK});
"""

DOWNGRADE_COLUMN = """
alter table public.flashcards drop constraint if exists flashcards_anki_source_check;
alter table public.flashcards drop column if exists source_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_POLICIES)
    op.execute(DOWNGRADE_COLUMN)
