# Segment 21 — M6: `author` graph, NBME lint, blind solve, human review UI, psychometrics; Settings + export-all

**Implementer:** `implementer-sonnet` (pipeline fully specified in `config/authoring_rules.md`). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** G (with S19, S20). Contracts on the ledger: with `seg19` the queue registry (`question_review`), `question_revisions` and the question editor calling `naadi_api/authoring/lint.py`. Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg21`).
**Depends on:** S02 (`questions` statuses `draft|auto_checked|in_review|published|retired`, `review`/`stats` jsonb hidden from clients, `sources.allow_ai_ingest`), S03 (admin role, `/settings/account`), S04 (Aegra background runs + crons, `llm.py` roles `author` and `reviewer`, `Served.review_not_independent`, job reserve), S07 (retrieval over AI-ingestible sources), S08 (attempts), S12b/S13/S14/S16/S18 (Settings groups they own), S19 (queues hub, question editor, coverage data).
**HANDOFF refs:** §7.3 (authoring pipeline; Law et al. evidence: expert review mandatory), §7.2 (coverage targets), §12 (every published question passes the pipeline), §8 Settings row (pen-only toggle, offline size, export all data as JSON + PDFs), §6.1 author/reviewer roles. `config/authoring_rules.md` Parts A–C. `docs/AGENTS.md` → `author`. Boards: `design/Settings.dc.html`, `design/Qbank.dc.html` (item preview), `design/System.dc.html`.

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `question_reviews` (backend-only, admin select): `question_id`, `reviewer_user_id`, `round`, `ratings jsonb` (accuracy, single best answer, cognitive level, flaws[], difficulty, taxonomy ok), `decision ('approve'|'revise'|'reject')`, `created_at`; unique `(question_id, reviewer_user_id, round)`.
- `authoring_runs` (backend-only): `id`, `cell` (exam, system, discipline, topic), `objective`, `difficulty`, `author_model`, `prompt_hash`, `excerpt_ids`, `lint jsonb`, `solver jsonb`, `outcome`, `created_at`.
- `question_stats` view/materialised job output in `questions.stats` (p-value, point-biserial, distractor rates and point-biserials, median time, report count, N, flags); `stats` stays hidden from clients.
- `export_jobs` (user table, client select): `id`, `user_id`, `status`, `include_vault bool`, `storage_path`, `bytes`, `expires_at`, `error`.
- `app_config.authoring`: `nightly_enabled`, `max_items_per_night: 10`, `max_llm_calls_per_night: 40`, `lint_retries: 2`, `psychometrics_min_n: 30`, `act_min_n: 100`; `ui_copy` `authoring.*`, `settings.*`, `export.*`.
### Pipeline (`naadi_api/authoring/`, graph `author` in `aegra.json`; admin trigger + nightly cron 22:41 UTC within the job reserve)
1. **Select**: coverage gaps from S19 (topics with < 15 published questions), or the admin's chosen cell + objective + difficulty.
2. **Ground**: excerpts only from sources with `allow_ai_ingest` true and public-domain / CC BY / Naadi-original licences (never user uploads, never link-only sources).
3. **Draft**: `author` role (purpose `job`) with Part A as the system prompt → pydantic item (stem, lead-in, options A–E, key, explanation, why-not per option, objective, taxonomy, difficulty, cited excerpt ids). Logged in `review.authorModel` + `authoring_runs`.
4. **Lint** (`lint.py`, also `tools/lint_items.py` CLI): every Part B rule with its id and severity, pure Python; ERROR → regenerate (≤ `lint_retries`) then discard.
5. **Blind solve**: `reviewer` role sees stem + options only (no key, no explanation), B01–B04; disagreement with the key or any ERROR → back to draft with the reason; WARNs attached. `review_not_independent` from `llm.py` marks the item and blocks auto-advance.
6. **Human review** (status `in_review`): two different admins review independently and blind to each other (a reviewer can't see the other's ratings until both submit); both approve → publishable; disagreement → third reviewer adjudicates. Approve requires all Part C ratings. Publish writes `review.humanReviewers[]`, a `question_revisions` snapshot, status `published`, and re-embeds.
7. **Psychometrics** (nightly): first attempts only, tutor + timed pooled; at N ≥ 30 flag only (p < 0.20 or > 0.95, point-biserial < 0.05, distractor > key, time > 2× block median, ≥ 2 reports); never auto-retire below N = 100. Flags appear in the review queue.
- Seed questions (`humanExpertReviewed: false`) enter the same human-review queue in a separate "Seed spot-check" filter.
### Web
- **Review UI** (`/admin/review`, registered in S19's queues): item preview exactly as students see it (S08 renderer) with the key and explanations, lint and solver findings, rating form (Part C fields), decision, history; adjudication view; psychometrics panel per item with N and flags (words, not colour).
- **Settings** (`/settings/*`) per board, groups: Profile (S03), Pencil & ink (S12b), Study plan (S11/S16), Appearance (dark theme only; no light toggle until the token switch exists), Offline (S18), Assistant (S14 memory, S13 handwriting-search toggle), Data & export, About & privacy (S20). This segment builds the shell, Data & export and any group whose owner left it unbuilt; each group shows only once its segment's flag is on.
- **Export all**: one lime CTA "Export everything" → `POST /api/export` starts a background job that writes a ZIP to `user-uploads/{user_id}/exports/{id}.zip` (JSON per user table, the student's uploaded files and generated media; vault only when "Include Vault documents" is ticked) and returns a signed URL (24 h, then the object is deleted); the client also produces notes/notebooks PDFs with S13's exporter in the same flow. No answer keys of unattempted questions are exported (shared content is not exported at all).
### Guardrails (review blockers)
- No placeholder questions or seed of invented items; drafts, `review` and `stats` never reach student endpoints; no answer reaches a student before an attempt. No First Aid/UWorld/NBME text in prompts or excerpts; every item cites its `sources`.
- Only free chains via `llm.py` within the job reserve and the per-night caps; prompts hold no PII. Every UI string a `ui_copy` key; pydantic → OpenAPI → zod. Lime = one next action; status never colour-only.

## Tests
- Unit: every Part B rule with pass/fail fixtures (Naadi-original), retry/discard, blind-solve disagreement, `review_not_independent` blocks, two-reviewer blindness and adjudication state machine, psychometric formulas on synthetic attempts (N thresholds), export ZIP manifest.
- db: drafts and `in_review` items invisible to students via every endpoint (pool, block, search, cards); `question_reviews` admin-only; the same admin cannot fill both review slots; export job RLS and signed URL expiry.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`, LLM stubbed): admin triggers authoring for a cell → item reaches review → admin A approves, admin B approves → published and in the pool; disagreement → adjudication; Settings groups render; Export everything → ZIP downloads with JSON and files.
## Acceptance
1. Lint rules from `authoring_rules.md`; the blind solver is a different family; disagreements are flagged.
2. Two human approvals to publish; psychometrics flag after 30 responses.
3. Settings per board and export-all (JSON + files + notes PDFs); zero axe violations / console errors. M6's "200 new reviewed questions" is a content milestone that needs two human reviewers.

## Decisions (lead to check)
- Reviewers are admins (`ADMIN_EMAILS`). One admin approval publishes (the owner is an MBBS graduate and the only user); the linter, source check and model cross-check must all pass first, and a second reviewer is supported but optional (lead decision).
- New backend-only `question_reviews`, `authoring_runs`; user table `export_jobs`. Export ZIP is server-built; notes PDFs stay client-side (S13's exporter).
- Vault files are in the export only when the student ticks it. Seed questions go through the same human queue.
