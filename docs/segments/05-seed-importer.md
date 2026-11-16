# Segment 05 — M1: Seed importer + `app_config` (runtime configuration)

**Implementer:** `implementer-opus`. Reason: named high-risk (seed importer); every content screen depends on its output.
**Reviewer:** `reviewer-opus` (mandatory).
**Wave:** B (parallel with S04 agent platform and S06 courses/reader). Agree the **TipTap node schema** with `seg06-reader` (contract) before writing the converter, and the `app_config` keys (`models`, `budget`, `search`, `generation`) with `seg04-agents`.
**Depends on:** S02 (schema, RLS, buckets).
**HANDOFF refs:** §7.5 (nothing hardcoded; importer), §7.6 (seed import), §7.1 (license gates). Data: `data/`, `config/`.

## Source data (shapes verified)
- `data/chapters.json`: 14 items `{id, exam, order, kind ('chapter'|'mock_exam'), title, meta, sections[{id, heading, html, figureIds}], figureIds, questionIds}` → 12 chapters + `mock1`, `mock2`; **259 sections**.
- Section HTML uses: `p h3 b i sub sup br ul ol li table thead tbody tr th td div`, `<div class="pearl|trap|bridge">` callouts (70/53/15), `<span class="hy">` high-yield marks (537), and **95 `[[figure:ID]]` placeholders**.
- `data/figures.json`: 95 `{id, chapterId, caption, file}`; SVGs in `data/figures/`.
- `data/questions.json`: 685 `{id, chapterId, exam, number, block, stemHtml, options, answer, answerHtml, explanationHtml, whyNotHtml, tags, difficulty, source, review}`.
- `data/taxonomy.json`: 217 nodes `{id, kind, parent_id, label, exam, weight_min, weight_max, sort}`; `data/question_taxonomy_map.json`: `{questionId: [taxonomy ids]}`.
- `data/pathway/pathway_stages.json` (16), `pathway_tasks.json` (186), guides `00-overview.md` … `16-*.md`.
- `data/study_plan_step1_60d.csv`: the 60-day plan template.
- `config/models.yaml`, `config/sources.yaml`, `config/youtube_channels.yaml`, `config/authoring_rules.md`.

## Scope
### `apps/api/scripts/seed.py` (CLI) + `naadi_api/seed/` (library)
- `uv run python scripts/seed.py --data ../../data --config ../../config [--dry-run] [--only chapters,questions,...] [--report out.json]`.
- **Idempotent upserts by id** for everything; a second run changes nothing (report shows 0 changes). Transactional per entity group; resumable.
- **Figures:** upload each SVG to the `content-public` bucket via the official `supabase` SDK (content-type `image/svg+xml`; sanitise SVG: strip scripts, event handlers, external refs), compute SHA-256 and skip unchanged; upsert `media_assets` (`kind='svg'`, `source_id='naadi-original-v1'`, `review_status='approved'` so clients can see them under S02's RLS, caption, storage URL); `section_media` rows in placement order.
- **HTML → TipTap JSON:** convert each section's HTML into the agreed node schema (paragraph, heading, bulletList/orderedList/listItem, table/tableRow/tableHeader/tableCell, text marks bold/italic/subscript/superscript, `highlight{color:'lime', meaning:'key_fact'}` for `span.hy`, `callout{variant:'pearl'|'trap'|'bridge'}`, hardBreak, and `figure{mediaId}` replacing each `[[figure:ID]]`). The converter lives in **TypeScript** in `packages/shared` using `@tiptap/html` `generateJSON` with the same extension set the reader uses (one schema, no drift); `seed.py` calls it via a small Node script (`pnpm --filter @naadi/shared seed:convert`) or the converted JSON is generated at build time and checked in a test — choose and document. Keep `body_html` as fallback. Fail the import if any `[[figure:` remains or a node type is unknown.
- **Questions:** `stem_html`, `options`, `answer`, `answer_html`, `explanation_html`, `why_not_html`, `educational_objective` (if derivable), difficulty, tags, `review` (keep `humanExpertReviewed=false`), `taxonomy_ids` from the map, `status='published'`, `source_id='naadi-original-v1'`. Mock exam questions keep `block`.
- **Taxonomy, sources** (`config/sources.yaml` → `sources`; seed only rows whose license fields are present), **pathway stages + tasks** (keys as in the JSON), guides stored as `pathway_stages.summary_md` (overview) and a `pathway_guides` table if S02 lacks one (add via migration in this segment, with RLS read-only for clients).
- **Plan template:** the 60-day CSV into a `plan_templates` table (migration here; shared read-only) — S11's generator must reproduce it.
- **`app_config`:** seed keys from `config/*.yaml`: `models` (providers + role chains), `budget`, `search`, `generation`, `sources_policy`, plus `feature_flags` (assistant off until S14, ink lab on for admins), `ui.nav` (only existing routes), `exam_rules` (block sizes, per-question timing for tutor/timed/exam modes from HANDOFF §8), `storage` (bucket names), `ui_copy` (editable strings already used by the UI). History rows on change.
- **`app_config` API:** `GET /api/config/public` (non-secret keys the client needs: feature flags, nav, exam rules, ui copy), admin `GET/PUT /api/admin/config/{key}` with pydantic validation per key and history listing; server-side cache 60 s with invalidation on write.
- **Verification report:** counts per entity (expected: 14 chapters, 259 sections, 685 questions, 95 figures, 217 taxonomy nodes, 685 map entries, 16 stages, 186 tasks, 60 plan rows), unresolved references, license gate results, unchanged/inserted/updated per table; non-zero exit if anything is off.
- Runs: in CI against the `supabase/postgres` + storage-api stack (S02's harness), locally the same, and against the **real project from the Space** via an admin-only, idempotent `POST /api/admin/seed` job (background, status endpoint) so no secret leaves the Space — or as a one-off container command documented in `docs/RUNBOOK.md`. Verify the real project afterwards with S02's `live_check` extended for seed counts (Management API, read-only).

## Tests
- Unit: HTML→TipTap for every tag/class seen (golden files per sample), SVG sanitiser, CSV parser, taxonomy map resolution, license gate.
- Integration (db): full import into an empty DB → report matches expected counts; second run 0 changes; RLS: anon sees published content and approved figures only, never answer columns.
- Live: after deploy, admin seed job completes; read-only check confirms counts on the real project.

## Acceptance criteria
1. `--dry-run` writes nothing and prints the plan; real run imports all expected counts; second run is a no-op.
2. No `[[figure:` remains; every figure node resolves to an approved `media_assets` row whose Storage object exists.
3. `app_config` seeded from `config/*.yaml`, editable via the admin API with history and 60 s cache; nothing model-, budget- or copy-related is hardcoded in `apps/web`.
4. The real project contains the seed (verified read-only), and `/api/health` stays healthy.
