# Segment 19 — Admin: console, TipTap chapter editor, media/question CRUD, `app_config` editor, coverage, agent runs, approval queues

**Implementer:** `implementer-opus` (named high-risk: admin editor; it writes shared content every student sees and must keep the licence gates and answer rules). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** G (with S20, S21). Post the **approval queue contract** on the ledger on day one (`naadi_api/admin/queues.py`: `register_queue(kind, list_fn, approve_fn, reject_fn)`; web `registerQueueView`); S20 registers `youtube_candidates`, S21 registers `question_review`; this segment registers `generated_media` (moved from S15b), `pathway_checks` (S17) and `report_issues`. With `seg21`: the question editor calls S21's `naadi_api/authoring/lint.py` once it lands. Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg19`).
**Depends on:** S02 (`chapters`, `chapter_sections`, `chapter_section_revisions`, `media_assets`, `section_media`, `questions`, `app_config`, `app_config_history`, `report_issues`, buckets), S03 (server-side admin gate, `/admin` route guard), S04 (Aegra runs/crons, `llm_usage`, `agent_health`, `search_usage`), S05 (`config_api/schemas.py` per-key models, 60 s cache, `seed/svg.py` sanitiser, HTML→TipTap), S06 (`ContentRenderer` node schema), S07 (re-embed on content change), S15b, S17.
**HANDOFF refs:** §7.5 (admin console, nothing hardcoded, live with no redeploy, `app_config` with history, 60 s cache), §7.1 (licence gates block publishing), §7.2 (coverage goals: 1 section, ≥ 1 approved video, ≥ 15 questions, ≥ 10 cards per topic; heat-map by system × discipline), §5.4 (YouTube), §12 ("Report an issue"). No admin board exists: build from `design/System.dc.html` (tokens, tiles, chips, buttons), `design/Settings.dc.html` (grouped forms), `design/Library.dc.html` (upload zone), `design/Chapter.dc.html` (editor surface), `design/Results.dc.html` (heat-map and bars styling).

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `chapter_section_revisions` gains `status ('draft'|'published')`, `parent_revision_id`; the section's live body is the latest published revision (drafts are admin-only). `chapters`/`questions` status changes are logged in `admin_audit` (backend-only: actor, action, ref_type, ref_id, before/after hash, at).
- `question_revisions` (backend-only, admin select): full row snapshot per edit, for audit and rollback (S21 uses it).
- Client privileges unchanged: every admin write goes through the API with the service role after `require_admin`; RLS never grants writes on shared content to `authenticated`.
### API (`naadi_api/admin/`, every route `require_admin`, rate limited, audited)
- Chapters/sections: create, edit metadata, reorder, status `draft → in_review → published → retired`; section save = new revision (`origin 'admin'`), publish, diff between revisions, rollback (a new revision copying an old one). Body validated against S06's node schema; unknown nodes refused. Re-embed the section on publish (S07 job).
- Media: figure upload (SVG through `seed/svg.py` sanitiser; raster → WebP), PDF upload to `content-private`, YouTube add by URL (oEmbed + `videos.list` `status.embeddable`), each requiring `source_id`; attach to sections (`section_media`, `start_s`/`end_s`). Publishing is blocked by the `sources_policy` gates (no licence; NC/ND on paid pages; share-alike in proprietary; non-AI-ingest into RAG) with a structured reason.
- Questions: CRUD with the full item form (stem, 5 options, key, explanations, why-not, objective, difficulty, taxonomy, source); status workflow (S21 adds lint + reviews); preview renders as the student sees it.
- `app_config`: list keys, `GET/PUT /api/admin/config/{key}` validated by the key's pydantic model, history list with diff, revert to a history entry; cache invalidated so the change is live within 60 s on every worker.
- Coverage: `GET /api/admin/coverage?exam=` → per taxonomy topic counts (published sections, approved videos, published questions, cards) vs the §7.2 goals, aggregated to a system × discipline grid.
- Agent ops: `GET /api/admin/agent/runs?graph=&status=` (Aegra via `langgraph-sdk` server-side, metadata only, never message content), cancel a run, list crons with next run and last result, `llm_usage` per day × provider × purpose vs caps, Tavily credits, `agent_health`.
- Queues: `GET /api/admin/queues` (counts per registered kind), `GET /api/admin/queues/{kind}`, approve/reject with a note.
- Report issues: triage list (open → triaged → resolved/dismissed) with a deep link to the item.
### Web (`/admin/*`, lazy-loaded chunk, never in the student bundle's initial load)
- **Chapter editor**: TipTap 3.34.0 with the same nodes `ContentRenderer` renders (paragraph, headings, lists, table, callout `pearl|trap|bridge`, figure picker, video, pdf, question-embed, mermaid, math), side-by-side live preview with `ContentRenderer`, revision history with diff and rollback, autosaved drafts, Publish as the one lime CTA.
- Media library, YouTube add dialog (shows title, channel, embeddable, source), question editor with preview, config editor (form per schema, JSON view, diff before save), coverage heat-map (cells with counts and a word: "Gap", "Thin", "Covered"; never colour only), agent runs/crons/usage tables, queues hub, report-issue triage.
- States: loading, empty, error, permission (non-admins never see `/admin` links; the API returns 403).
### Guardrails (review blockers)
- No placeholders, sample content or fake rows; every UI string a `ui_copy` key (`admin.*`); pydantic → OpenAPI → zod.
- Question previews and lists are admin-only; no admin endpoint feeds a student route; published edits keep S08's hidden-column rules. Every asset needs a `sources` row; no First Aid/UWorld/NBME text; YouTube only via the IFrame player.
- No LLM call is added; agent ops show metadata, never prompts or messages. Lime = one next action per view; status never colour-only.

## Tests
- Unit: node-schema validation, revision diff/rollback, licence gate matrix, coverage aggregation, config schema validation + revert, audit rows.
- db: every admin route 403 for a non-admin and anon; `authenticated` still cannot write shared tables; drafts invisible to students; config change visible through `/api/config` within 60 s.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): admin adds a chapter with a section, a figure, a PDF, a YouTube video and a question → each appears to a student session with no redeploy; edit + rollback a section; edit `feature_flags` → history shows it → revert; coverage cell updates; non-admin sees no admin links and gets 403.
## Acceptance
1. Admin adds a chapter, PDF, figure, YouTube video and question; each is live with no redeploy.
2. `app_config` edit with validation, history, revert and the 60 s cache; licence gates block publishing with a reason.
3. Coverage heat-map, agent runs/crons and approval queues visible to admins only; zero axe violations / console errors.

## Decisions (lead to check)
- **Contract change:** section revisions get `status` (draft/published); the live body is the latest published revision. New backend-only `admin_audit` and `question_revisions`.
- One approval-queue registry owns every queue (generated media, YouTube, questions, pathway checks, reports).
- No admin design board: built from System/Settings/Library/Chapter boards.
