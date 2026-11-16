# Segment 17 — M5: Pathway (stages, tasks, re-verify badges, vault, cost tracker, `pathway_verify` cron)

**Implementer:** `implementer-sonnet` (CRUD on seeded data; vault policies exist from S02). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** F (with S16, S18). Contracts on the ledger: with `seg18` the outbox entities `user_pathway_task` and `cost` (LWW per row with `client_updated_at_ms`) and that vault files are never cached offline; with `seg15` placement `cost_entry` (fee receipts → cost proposal) and the vault hand-off from the sensitivity gate. Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg17`).
**Depends on:** S02 (`pathway_stages`, `pathway_tasks`, `pathway_guides`, `user_pathway_tasks`, `vault_documents`, `costs`, bucket `vault` + storage policies), S03 (admin gate), S04 (Aegra crons, `llm.py` structured role, `search.py` with `include_domains`), S05 (16 stages, 186 tasks, guides seeded), S11 (Today pathway strip, shown when the flag is on), S14 (tutor rule 5: pathway answers from `pathway_tasks` + official links), S15 (vault hand-off, placements).
**HANDOFF refs:** §8 Pathway row (`last_verified` + official link, > 90-day "re-verify" badge, vault private, costs by stage), §6.5 rule 5, §13 risk 7 (pathway facts change monthly), §7.1 (sources). `docs/AGENTS.md` → `pathway_verify`, principle 4 (vault never reaches a model). Boards: `design/Pathway.dc.html`, `design/Main.dc.html` + `design/PhoneToday.dc.html` (pathway strip).

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `pathway_task_checks` (shared, backend-only, admin select): `id`, `task_id`, `checked_at`, `url`, `http_status`, `content_hash`, `changed bool`, `finding jsonb` (`{status: 'unchanged'|'changed'|'unreachable'|'moved', summary_key, quotes[{text ≤ 300 chars, url}]}`), `resolved_by`, `resolved_at`. Clients have no access.
- `pathway_tasks` gains `content_hash`, `needs_review bool default false` (API/cron-only). `user_pathway_tasks` and `costs` gain `client_updated_at_ms bigint` and `updated_at`; `costs` gains `stage_key` (denormalised from the task, or chosen when no task). `vault_documents` gains `bytes`, `mime`, `sha256`.
- `app_config.pathway`: `reverify_days: 90`, `verify_max_tasks_per_run: 60`, `verify_max_llm_calls: 20`, `signed_url_seconds: 60`, `currencies: [USD, INR]`; flag `pathway` on at the end; `ui.nav` gains Pathway; `ui_copy` `pathway.*`, `vault.*`, `costs.*`.
### API (`naadi_api/pathway/`)
- `GET /api/pathway` (stages with tasks, the student's status/due/notes per task, `last_verified`, `reverify` = older than `reverify_days` or `needs_review`, official links, guide), `PATCH /api/pathway/tasks/{id}` `{status, due, notes, client_updated_at_ms}` (LWW), `PUT /api/me/pathway-stage`.
- Vault: `POST /api/vault/uploads` (signed upload URL to `vault/{user_id}/{id}`, ≤ 20 MB, PDF/JPEG/PNG/HEIC sniffed from bytes), `GET /api/vault` (metadata), `GET /api/vault/{id}/url` (signed URL, `signed_url_seconds`), `PATCH` (title, type, `expires_on`), `DELETE` (object + row). Vault routes never import `llm.py`, `search.py` or the embeddings package (an import-graph test enforces it); bodies never logged.
- Costs: `GET/POST/PATCH/DELETE /api/costs`; `GET /api/costs/summary` → totals by stage and by currency (no FX conversion), planned (from `pathway_tasks.cost_usd`) vs paid.
- Admin: `GET /api/admin/pathway/checks?open=1`, `POST /api/admin/pathway/tasks/{id}/verify` (sets `last_verified` to today, clears `needs_review`, resolves open checks), `PATCH /api/admin/pathway/tasks/{id}` (detail, cost, official URL). S19 shows this list in its queues.
### `pathway_verify` graph (Aegra cron, weekly Monday 04:13 UTC)
- For up to `verify_max_tasks_per_run` tasks (oldest `last_verified` first) whose `official_url` host is in `search.include_domains`: GET the page through `httpx` (no SDK exists for a plain page fetch; one-line comment), 15 s timeout, robots-respecting user agent, normalise to text, hash. Unchanged → check row `unchanged`. Changed, moved (redirect to another path) or unreachable → `needs_review = true`; for changed pages, one `structured`-role call (purpose `job`, ≤ `verify_max_llm_calls` per run) compares the task's stored facts (title, detail, cost) with the new text and returns pydantic `{still_accurate: bool, differences[], quotes[]}`; every quote must appear verbatim in the fetched text or it is dropped. Tavily is not used by the cron (credit budget); the student's own pathway questions still use it via the tutor.
- Never touches user data; no PII exists in its inputs.
### Web
- **Pathway** (`/pathway`) per board: 7-stage route header with the student's current stage, stage cards with task lists (status chips with words: To do, Doing, Done, Not needed), each task showing `last_verified` ("Checked 12 Oct 2026") and its official link, the "Re-verify" badge (amber + word + icon) when stale or flagged, task sheet (detail, due date, notes, cost), official-source check strip ("Checked against ecfmg.org …"). One lime CTA: the next undone task of the current stage.
- **Vault** panel: list with type, expiry chip ("Expires in 30 days" in amber + word under 60 days), upload (camera on iPad), open via signed URL in a new tab, delete with confirm; banner "Vault files are never sent to AI".
- **Cost tracker**: entries by stage, totals per stage and currency, planned vs paid, add/edit sheet.
- Today pathway strip turns on with the flag. Admin-only "Mark verified" action on the task sheet.
- States: loading, empty (no costs, empty vault), error, offline (task status and costs queue with "will sync"; vault upload and open need a connection: disabled with reason).
### Guardrails (review blockers)
- No placeholders or invented pathway facts: everything renders from seeded rows with their `last_verified`; every UI string a `ui_copy` key; pydantic → OpenAPI → zod.
- Vault never reaches any model, search provider, embeddings, logs or the offline cache. Only free chains via `llm.py`; the cron's inputs contain no PII.
- Every quoted official text keeps its URL; links go to official domains only. Lime = one next action, amber + word for re-verify; status never colour-only.

## Tests
- Unit: re-verify rule (day 90 vs 91, flagged), cost totals by stage and currency, verify cron with fixture pages (unchanged / changed / moved / 404), quote verbatim check drops invented quotes, LLM cap per run, import-graph test for the vault module.
- db: RLS on user tables and storage policies (user B cannot list, sign or download A's vault objects; anon nothing); `pathway_task_checks` closed to clients; admin verify endpoint 403 for non-admins.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): set a task Done → reload → persists; a > 90-day task shows the badge with its word; upload a vault file → open via signed URL → delete; add costs in two stages → totals.
## Acceptance
1. Every task shows `last_verified` and an official link; > 90-day or flagged tasks show "Re-verify".
2. The vault is private (RLS + signed URLs) and never sent to a model; costs total by stage.
3. `pathway_verify` flags stale tasks with official quotes within budget; matches the Pathway board; zero axe violations / console errors.

## Decisions (lead to check)
- The weekly cron fetches official pages directly (`httpx`) instead of Tavily, to keep Tavily's 950 credits for the student; at most 20 LLM calls per run.
- New backend-only `pathway_task_checks`; `last_verified` changes only by an admin confirm, never by the cron.
- Costs are summed per currency with no conversion; `costs.stage_key` added.
