# Segment 02 — M1: Alembic migrations, RLS, storage buckets, migration and RLS tests

**Implementer:** `implementer-opus`. Reason: auth/RLS is named high-risk; every user table's privacy depends on this segment.
**Reviewer:** `reviewer-opus` (mandatory).
**Depends on:** 01 (apps/api scaffold, settings, CI with the `supabase/postgres` service container, Space deploy).
**HANDOFF refs:** §2.1 (Supabase Free limits), §7.5 (storage layout), `db/schema.sql` (data contract), §11 M1. Decisions: `docs/PLAN.md` → "Alembic", "Agent capabilities", "LLM providers"; `docs/AGENTS.md` (knowledge base, ingest, chapter revisions, generation).

## Goal
The real Supabase project has the full Naadi schema, created only by Alembic, with RLS policies in the same migrations, the four Storage buckets with their policies, and tests that prove cross-user access is denied.

## Scope

### Alembic setup (`apps/api/migrations/`)
- `alembic.ini` + `env.py` reading `DATABASE_URL` from `naadi_api.settings`; **`version_table = "naadi_alembic_version"`** (Aegra will keep its own `alembic_version` in the same database from segment 04).
- Migrations are hand-written SQL via `op.execute` where clearer (RLS, policies, functions), SQLAlchemy ops elsewhere. Each has a real `downgrade()`.
- Psycopg 3 driver; works through the Supabase **session pooler** (port 5432).

### Migration 0001 — core schema (from `db/schema.sql`, corrected)
- Extensions `vector`, `pg_trgm` (in the `extensions` schema as Supabase expects, with `search_path` handled).
- All tables in `db/schema.sql`, with these corrections and additions:
  - `pathway_stages.key` values come from `data/pathway/pathway_stages.json` (16 keys), not the old comment examples.
  - `chapters.kind` gains `user_kb` (knowledge-base documents); `uploads.kind` widened to `pdf, docx, pptx, xlsx, csv, txt, md, html, epub, rtf, image, audio, video, link, anki, zip, paste`.
  - `embeddings.ref_type` adds `url`, `ink`, `kb_doc`; keep `halfvec(384)` + HNSW (`halfvec_cosine_ops`) + GIN on `tsv`.
  - `llm_usage` adds `provider text not null` (`nvidia` | `openrouter`), `run_id text`, `latency_ms int`; index `(provider, created_at)`.
  - New `kb_sources` (id uuid, user_id, kind `url|file|paste`, url, title, upload_id, status `processing|ready|failed|needs_confirmation`, error, content_hash, created_at, updated_at).
  - New `ingest_items` (id uuid, user_id, upload_id, kb_source_id, detected_kind, sensitivity `normal|personal`, proposed_placement jsonb, confirmed_placement jsonb, status `received|extracting|needs_confirmation|placing|ready|failed|vaulted`, error, created_at, updated_at).
  - New `chapter_section_revisions` (id bigserial, section_id → chapter_sections, body jsonb, body_html, author_user_id, origin `admin|assistant|seed`, note, created_at); rollback = copy a revision back.
  - New `generated_media` (id uuid, user_id, kind `image|video`, prompt, model, storage_path, width, height, duration_s, ai_generated boolean not null default true check (ai_generated), approved_for_publish boolean default false, created_at).
  - New `report_issues` (id, user_id, ref_type, ref_id, body, status, created_at) for the "Report an issue" button.
  - `profiles.user_id` references `auth.users(id) on delete cascade`; every user-owned table's `user_id` likewise.
- Helpful indexes for the hot paths (attempts by user/question, card_reviews by user/due, notes by user/anchor, plan_items by user/day, embeddings by owner/ref).

### RLS (same migration as each table)
- **User-owned tables** (`profiles, attempts, blocks, card_reviews, notes, notebooks, ink_layers, uploads, plan_items, user_pathway_tasks, vault_documents, costs, assistant_threads, kb_sources, ingest_items, generated_media, report_issues`): RLS enabled and forced; separate `select/insert/update/delete` policies `to authenticated` using `(select auth.uid()) = user_id` (the `select` wrapper for per-statement evaluation). No access for `anon`.
- **Shared content** (`chapters, chapter_sections, questions, media_assets, section_media, taxonomy_nodes, sources, pathway_stages, pathway_tasks, flashcards`): RLS on; `select` to `anon, authenticated` only where `status = 'published'` (where the table has a status) **or** `owner_user_id = auth.uid()` (private uploads/KB chapters and user flashcards). Writes only by the service role (backend). `questions` columns `answer`, `answer_html`, `explanation_html`, `why_not_html` must not be readable by clients at all: revoke column privileges from `anon`/`authenticated` (the API serves them after an attempt).
- **Embeddings:** select only where `owner_user_id is null` (shared, AI-ingestible) or `= auth.uid()`; writes service role only.
- **Admin-only tables** (`app_config`, `app_config_history`, `chapter_section_revisions`, `search_cache`, `llm_usage`): no client access; backend (service role) only.
- `app_config` history via trigger on update.

### Migration 0002 — Storage buckets and policies
- Buckets: `content-public` (public read), `content-private` (signed URLs only), `user-uploads` (private), `vault` (private), `generated-media` (private). Size limits and allowed MIME types per bucket.
- `storage.objects` policies: `user-uploads`, `vault`, `generated-media` → authenticated users can read/write only under `{auth.uid()}/…`; `content-*` writes service role only. Vault objects are never readable by the service role's AI paths (enforced in code in later segments; documented here).

### Applying to the real project
- The Space container runs `alembic upgrade head` at startup under a Postgres **advisory lock** (so two workers never race), then starts the app; failure stops startup with a clear log line (no secrets).
- From the sandbox, verify the real project's resulting schema, policies and buckets through the Supabase Management API (`SUPABASE_ACCESS_TOKEN`, HTTPS), and record the check in the report.

## Tests
- **Migration test** (pytest, marker `db`, CI against the `supabase/postgres:17.11.0.019` service container and locally via the sandbox's dockerd): empty → `upgrade head` → insert a minimal fixture (two users in `auth.users`, one row per user table) → `downgrade -1` → `upgrade head` again; assert `naadi_alembic_version` exists and `alembic_version` is untouched.
- **RLS suite** (marker `db`): simulate users with `set local role authenticated; set local request.jwt.claims = '{"sub":"<uuid>","role":"authenticated"}'`. For **every** user-owned table: user B cannot select, insert-as-A, update or delete A's rows; user A can do all four on their own rows. `anon` cannot read any user table. Shared content: anon sees only published rows; a private (`owner_user_id`) chapter is invisible to other users; question answer columns are not selectable by `anon`/`authenticated`. Storage: user B cannot read A's objects in `user-uploads`, `vault`, `generated-media`.
- A test that fails if a new table is added without RLS (query `pg_class` for tables in `public` without `relrowsecurity`).
- Live: after deploy, `/api/health` stays `database: ok`, and the Management API check confirms `naadi_alembic_version` is at head on the real project.

## Acceptance criteria
1. All migrations apply cleanly to an empty Supabase Postgres and to the real project; downgrade works one step.
2. Every user-owned table has RLS with tests proving cross-user denial; no table in `public` lacks RLS.
3. Question answer/explanation columns are unreadable by client roles.
4. Buckets and storage policies exist and are tested.
5. CI green (db tests run in the service container); live Space healthy after migrating at startup.
6. `db/schema.sql` updated to match the migrations (documentation of the contract), with the pathway-key comment fixed.

## Out of scope
Auth UI and JWT verification in the API (03), seed data (05), Aegra tables (04).
