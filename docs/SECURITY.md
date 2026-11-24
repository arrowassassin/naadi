# Naadi security

The security model, the controls in place and where each one is tested. Operational procedures (rotating secrets,
restore) are in [`RUNBOOK.md`](RUNBOOK.md); secret names in [`SECRETS.md`](SECRETS.md); the system layout in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Threat model

**What we protect:** students' study records, notes, ink, uploads and personal documents (`vault`); their identity
(email, profile); the question bank's keys and explanations before an attempt; the free-tier quotas (LLM calls,
Tavily credits, Supabase limits) that keep the app running; the integrity of published content.

| Threat | Main controls |
|---|---|
| Another student reads or changes my data | Supabase JWT verified per request; RLS on every public table with owner policies; server-authoritative tables written only by the API (`tests/db/test_rls*.py`, `live_check.py`) |
| A student sees answers before attempting | Answer columns revoked from client roles; only qbank + seed modules read them (test pins the list); attempt-gated endpoints; offline store holds no keys |
| XSS / script injection | CSP `script-src 'self'`, `style-src 'self'`, no `unsafe-*`; content rendered from TipTap JSON through `ContentRenderer`; HTML fragments through `SafeHtml`'s allowlist (inert parse); Trusted Types report-only with a same-origin default policy; Mermaid and YouTube in opaque-origin sandboxed frames |
| Session theft through third-party script | No third-party script in our origin (YouTube's API runs only in the sandboxed player page); `connect-src` limited to us + the Supabase project |
| Clickjacking | `frame-ancestors 'self' https://huggingface.co` (the HF page frames Spaces); COOP same-origin |
| Quota exhaustion / abuse | Per-user and per-IP buckets on every route; per-client floor; Aegra run limit; LLM budget guards in `llm.py`; Tavily monthly stop |
| SSRF through fetchers | Fixed provider hosts in `search.py` and the YouTube client; `pathway_verify` follows only the task's official domain (off-domain redirect refused, robots respected, 2 MB cap); a pasted link is read through Tavily's extract (S15), so the Space never fetches an arbitrary URL |
| Data leaving to model providers | PII replaced before every call (`llm_privacy.py`: names, emails, phones, IDs, profile strings); vault material refused at runtime (`assert_no_vault`); only `:free` models with `data_collection: deny` / ZDR where the provider supports it |
| Leaked secrets | Secrets only from the environment via `settings.py`; scrubbed from errors and logs (`scrub.py`); CI scans e2e artifacts for credentials; nothing secret in workflows except the deploy token |
| Lost data | Encrypted nightly backups to a private dataset, weekly Storage tarball, tested restore (RUNBOOK) |
| Supply chain | Exact pins; hash-locked Python install on the Space; `pnpm audit --prod --audit-level high` and `pip-audit --strict` on every push; CycloneDX SBOMs; Actions pinned by commit SHA (test) |

## Privacy

- **PII scrubbing:** every model call goes through `naadi_api/llm.py`, which applies `llm_privacy.scrub_messages` (the
  student's name, email, phone numbers, long numeric and document IDs, UUIDs and any string from their `profiles` row
  become placeholders) and refuses vault material. Web searches are scrubbed the same way before leaving.
- **Vault:** personal documents live in the private `vault` bucket, served by 60-second signed URLs with
  `Cache-Control: no-store`; never cached offline, never embedded for search, never sent to a model.
- **Logs:** one JSON line per request with method, path (no query string), status and duration; request and response
  bodies, `authorization`, cookies, tokens and query strings are dropped by the logger (`logs.py`); CSP reports are
  logged as summaries (directive, disposition, blocked origin, document path), never their samples.
- **Backups** contain user data and are encrypted to the owner's age key before they leave the Space; the dataset is
  private and the job refuses a public one.

## Headers (`naadi_api/security.py`, tested in `tests/test_security_headers.py`)

| Header | Value (production) |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data: blob: <Wikimedia, PMC bucket> <Supabase>; connect-src 'self' <Supabase https + wss>; media-src 'self' blob: https://upload.wikimedia.org <Supabase>; frame-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self' https://huggingface.co; upgrade-insecure-requests; report-uri /api/csp-report; report-to csp` |
| `Content-Security-Policy-Report-Only` | `require-trusted-types-for 'script'; report-uri /api/csp-report; report-to csp` (not on the sandbox documents) |
| `Reporting-Endpoints` | `csp="/api/csp-report"` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(self)` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cache-Control` on `/api/*` | `no-store` |

Sandboxed documents: `/sandbox/mermaid.html` (`style-src 'self' 'unsafe-inline'`, `sandbox allow-scripts`) and
`/sandbox/youtube.html` (`script-src 'self' https://www.youtube.com`, `frame-src https://www.youtube-nocookie.com`,
`sandbox allow-scripts`) run in opaque origins: they cannot read the app's storage or cookies and talk to the page
only through a validated postMessage bridge.

**Trusted Types rollout.** Step 1 (now): report-only, with two policies in `apps/web/src/security/trustedTypes.ts`: a
`default` policy that accepts script URLs on our own origin only, and a module-private `naadi-inert-html` policy used
only to parse HTML fragments into an inert document. `e2e/csp.spec.ts` opens every main route on the production
server in WebKit, Chromium and Firefox and requires zero enforced violations; report-only reports are attached to the
test. Step 2 (after a release with an empty report log, owner seg22 → lead): move
`require-trusted-types-for 'script'; trusted-types default naadi-inert-html` into the enforcing policy.

**Reports** go to `POST /api/csp-report` (both the `application/csp-report` and Reporting API shapes), capped at
16 KB, limited per client (30/min), deduplicated per minute, always answered 204.

## Rate limits

Every operation carries a limiter (`apps/api/tests/test_rate_limit_audit.py` walks every route and fails on a route
without one, on an anonymous route not keyed per client, and on a heavy route over its ceiling). Layers:

1. **Per-client floor** (`ClientFloorMiddleware`): 600 requests/min per client IP for everything but hashed assets.
   The key is the right-most `X-Forwarded-For` entry (the one Hugging Face's proxy appends), not the client-controlled
   first entry. Direct loopback connections (local runs, CI, the container's own probes) are not counted.
2. **Per-user buckets** on every authenticated route, named after what they protect; **per-IP buckets** on the
   anonymous ones (`public_read`, `health`, `csp_report`, the ICS feed).
3. **Admin:** every admin route also counts against `admin_any` (120/min) and every admin write against `admin_write`
   (60/min), in `require_admin`.
4. **Heavy-route ceilings** (per key per minute): ingest/upload/import 30, export 30, generation 30, admin writes 60,
   account writes 60; everything else at most 600.
5. **Agent Protocol** (Aegra's routes, `naadi_api/agent/limits.py`): 600 requests/min and 30 new runs/min per
   credential; each run is further bounded by the LLM budget (`config/models.yaml`: rpm, daily cap, 4 calls per turn).

Regenerate the table below with `cd apps/api && uv run python -m tests.test_rate_limit_audit`
(state at segment 22, 196 operations; the test, not this table, is the source of truth):

| Method | Path | Buckets (limit/window, key) | Class | Ceiling/min |
|---|---|---|---|---|
| GET | `/api/admin/agent/crons` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/agent/runs` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/agent/runs/{thread_id}/{run_id}/cancel` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/agent/usage` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/audit` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/chapters` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/chapters` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/chapters/reorder` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/chapters/{chapter_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| PATCH | `/api/admin/chapters/{chapter_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/chapters/{chapter_id}/sections` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/chapters/{chapter_id}/sections/reorder` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/chapters/{chapter_id}/status` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/config` | `admin_any` 120/60s user, `admin` 60/60s user | - | 600 |
| GET | `/api/admin/config/{key}` | `admin_any` 120/60s user, `admin` 60/60s user | - | 600 |
| PUT | `/api/admin/config/{key}` | `admin_any` 120/60s user, `admin_write` 60/60s user, `admin` 60/60s user | admin write | 60 |
| GET | `/api/admin/config/{key}/history` | `admin_any` 120/60s user, `admin` 60/60s user | - | 600 |
| POST | `/api/admin/config/{key}/revert` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/coverage` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/embeddings` | `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/embeddings/reindex` | `admin_any` 120/60s user, `admin_write` 60/60s user, `admin_index` 6/60s user | admin write | 60 |
| GET | `/api/admin/generated` | `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/generated/{media_id}/approve` | `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/media` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/media/files` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/media/youtube` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/media/youtube/lookup` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| PATCH | `/api/admin/media/{media_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/media/{media_id}/review` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/overview` | `admin_any` 120/60s user, `admin` 60/60s user | - | 600 |
| GET | `/api/admin/pathway/checks` | `pathway_admin` 30/60s user, `admin_any` 120/60s user | - | 600 |
| PATCH | `/api/admin/pathway/tasks/{task_id}` | `pathway_admin` 30/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/pathway/tasks/{task_id}/verify` | `pathway_admin` 30/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/questions` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/questions` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/questions/{question_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| PUT | `/api/admin/questions/{question_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/questions/{question_id}/lint` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/questions/{question_id}/revisions` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/questions/{question_id}/status` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/queues` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/queues/{kind}` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/queues/{kind}/{item_id}/approve` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/queues/{kind}/{item_id}/reject` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/reports` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/reports/{report_id}/status` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/review/gaps` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/review/items` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/review/items/{item_id}` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/review/items/{item_id}/checks` | `review_heavy` 20/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/review/items/{item_id}/psychometrics` | `review_write` 60/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/review/items/{item_id}/resubmit` | `review_write` 60/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/review/items/{item_id}/reviews` | `review_write` 60/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/review/psychometrics/run` | `review_heavy` 20/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/review/runs` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/review/runs` | `review_heavy` 20/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/review/summary` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/review/taxonomy` | `review_read` 300/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/revisions/{revision_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/revisions/{revision_id}/diff` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/revisions/{revision_id}/rollback` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| PATCH | `/api/admin/sections/{section_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| DELETE | `/api/admin/sections/{section_id}/draft` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| PUT | `/api/admin/sections/{section_id}/draft` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/sections/{section_id}/editor` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| DELETE | `/api/admin/sections/{section_id}/media/{media_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| PUT | `/api/admin/sections/{section_id}/media/{media_id}` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/sections/{section_id}/publish` | `admin_console` 600/60s user, `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/sections/{section_id}/revisions` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/seed` | `admin_any` 120/60s user, `admin_write` 60/60s user, `admin` 60/60s user | admin write | 60 |
| GET | `/api/admin/seed/runs` | `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/seed/runs/{run_id}` | `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/sources` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| GET | `/api/admin/taxonomy` | `admin_console` 600/60s user, `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/youtube/add` | `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| GET | `/api/admin/youtube/candidates` | `admin_any` 120/60s user | - | 600 |
| POST | `/api/admin/youtube/candidates/{media_id}/approve` | `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| POST | `/api/admin/youtube/candidates/{media_id}/reject` | `admin_any` 120/60s user, `admin_write` 60/60s user | admin write | 60 |
| DELETE | `/api/anki/decks` | `anki_import` 6/600s user | - | 600 |
| GET | `/api/anki/decks` | `anki_read` 120/60s user | - | 600 |
| GET | `/api/anki/export` | `anki_export` 10/600s user | export | 30 |
| POST | `/api/anki/import` | `anki_import` 6/600s user | ingest/upload/import | 30 |
| GET | `/api/blocks` | `qbank_read` 300/60s user | - | 600 |
| POST | `/api/blocks` | `qbank_write` 120/60s user | - | 600 |
| POST | `/api/blocks/mock` | `qbank_write` 120/60s user | - | 600 |
| POST | `/api/blocks/single` | `qbank_write` 120/60s user | - | 600 |
| POST | `/api/blocks/weak-spot` | `qbank_write` 120/60s user | - | 600 |
| GET | `/api/blocks/{block_id}` | `qbank_read` 300/60s user | - | 600 |
| POST | `/api/blocks/{block_id}/attempts` | `qbank_write` 120/60s user | - | 600 |
| PATCH | `/api/blocks/{block_id}/attempts/{question_id}` | `qbank_write` 120/60s user | - | 600 |
| GET | `/api/blocks/{block_id}/clock` | `qbank_read` 300/60s user | - | 600 |
| POST | `/api/blocks/{block_id}/finish` | `qbank_write` 120/60s user | - | 600 |
| GET | `/api/blocks/{block_id}/questions/{n}` | `qbank_read` 300/60s user | - | 600 |
| GET | `/api/blocks/{block_id}/results` | `qbank_read` 300/60s user | - | 600 |
| POST | `/api/blocks/{block_id}/sections/{n}/{action}` | `qbank_write` 120/60s user | - | 600 |
| POST | `/api/cards` | `cards_write` 240/60s user | - | 600 |
| GET | `/api/cards/due` | `cards_read` 300/60s user | - | 600 |
| GET | `/api/cards/stats` | `cards_read` 300/60s user | - | 600 |
| DELETE | `/api/cards/{card_id}` | `cards_write` 240/60s user | - | 600 |
| GET | `/api/cards/{card_id}` | `cards_read` 300/60s user | - | 600 |
| PATCH | `/api/cards/{card_id}` | `cards_write` 240/60s user | - | 600 |
| POST | `/api/cards/{card_id}/review` | `cards_write` 240/60s user | - | 600 |
| GET | `/api/chapters/{chapter_id}` | `courses_read` 240/60s user | - | 600 |
| POST | `/api/coach/suggestions/{suggestion_id}/accept` | `plan_write` 30/60s user | - | 600 |
| POST | `/api/coach/suggestions/{suggestion_id}/dismiss` | `plan_write` 30/60s user | - | 600 |
| GET | `/api/costs` | `costs_read` 300/60s user | - | 600 |
| POST | `/api/costs` | `costs_write` 300/60s user | - | 600 |
| GET | `/api/costs/summary` | `costs_read` 300/60s user | - | 600 |
| DELETE | `/api/costs/{cost_id}` | `costs_write` 300/60s user | - | 600 |
| PATCH | `/api/costs/{cost_id}` | `costs_write` 300/60s user | - | 600 |
| GET | `/api/courses` | `courses_read` 240/60s user | - | 600 |
| POST | `/api/csp-report` | `csp_report` 30/60s ip | - | 600 |
| POST | `/api/diagnostic` | `plan_write` 30/60s user | - | 600 |
| GET | `/api/exam/active` | `qbank_read` 300/60s user | - | 600 |
| GET | `/api/export` | `export_read` 240/60s user | - | 600 |
| POST | `/api/export` | `export_start` 6/3600s user | export | 30 |
| DELETE | `/api/export/{job_id}` | `export_read` 240/60s user | - | 600 |
| GET | `/api/export/{job_id}` | `export_read` 240/60s user | - | 600 |
| GET | `/api/generated` | `generation_read` 240/60s user | - | 600 |
| DELETE | `/api/generated/{media_id}` | `generation_write` 30/60s user | generation | 30 |
| GET | `/api/generated/{media_id}` | `generation_read` 240/60s user | - | 600 |
| GET | `/api/generation/status` | `generation_read` 240/60s user | - | 600 |
| GET | `/api/health` | `health` 60/60s ip | - | 600 |
| POST | `/api/ingest/batches/{batch_id}/confirm` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/batches/{batch_id}/start` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| GET | `/api/ingest/items` | `ingest_read` 600/60s user | - | 600 |
| DELETE | `/api/ingest/items/{item_id}` | `ingest_write` 30/60s user | - | 600 |
| GET | `/api/ingest/items/{item_id}` | `ingest_read` 600/60s user | - | 600 |
| POST | `/api/ingest/items/{item_id}/confirm` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/items/{item_id}/retry` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/items/{item_id}/start` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/items/{item_id}/undo` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/paste` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/uploads` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ingest/uploads/batch` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| GET | `/api/ingest/uploads/{upload_id}/file` | `ingest_read` 600/60s user | - | 600 |
| GET | `/api/ingest/uploads/{upload_id}/pages/{page}` | `ingest_read` 600/60s user | - | 600 |
| POST | `/api/ingest/urls` | `ingest_write` 30/60s user | ingest/upload/import | 30 |
| POST | `/api/ink/convert` | `ink_convert` 10/60s user | - | 600 |
| GET | `/api/ink/{surface_type}/{surface_id}` | `ink_read` 600/60s user | - | 600 |
| POST | `/api/ink/{surface_type}/{surface_id}/changes` | `ink_write` 300/60s user | - | 600 |
| GET | `/api/lab-values` | `qbank_read` 300/60s user | - | 600 |
| GET | `/api/library` | `ingest_read` 600/60s user | - | 600 |
| GET | `/api/me` | `me_read` 120/60s user | - | 600 |
| PATCH | `/api/me` | `me_write` 30/60s user | auth/account write | 60 |
| PUT | `/api/me/pathway-stage` | `pathway_write` 300/60s user | - | 600 |
| GET | `/api/nav` | `me_read` 120/60s user | - | 600 |
| GET | `/api/notebooks` | `notes_read` 600/60s user | - | 600 |
| POST | `/api/notebooks` | `notes_write` 600/60s user | - | 600 |
| DELETE | `/api/notebooks/{notebook_id}` | `notes_write` 600/60s user | - | 600 |
| GET | `/api/notebooks/{notebook_id}` | `notes_read` 600/60s user | - | 600 |
| PATCH | `/api/notebooks/{notebook_id}` | `notes_write` 600/60s user | - | 600 |
| POST | `/api/notebooks/{notebook_id}/pages` | `notes_write` 600/60s user | - | 600 |
| PUT | `/api/notebooks/{notebook_id}/pages/order` | `notes_write` 600/60s user | - | 600 |
| GET | `/api/notes` | `notes_read` 600/60s user | - | 600 |
| POST | `/api/notes` | `notes_write` 600/60s user | - | 600 |
| DELETE | `/api/notes/{note_id}` | `notes_write` 600/60s user | - | 600 |
| GET | `/api/notes/{note_id}` | `notes_read` 600/60s user | - | 600 |
| PATCH | `/api/notes/{note_id}` | `notes_write` 600/60s user | - | 600 |
| GET | `/api/onboarding` | `plan_read` 240/60s user | - | 600 |
| PUT | `/api/onboarding` | `plan_write` 30/60s user | - | 600 |
| GET | `/api/pathway` | `pathway_read` 300/60s user | - | 600 |
| PATCH | `/api/pathway/tasks/{task_id}` | `pathway_write` 300/60s user | - | 600 |
| GET | `/api/plan` | `plan_read` 240/60s user | - | 600 |
| DELETE | `/api/plan/calendar-feed` | `planner_feed` 10/60s user | - | 600 |
| GET | `/api/plan/calendar-feed` | `planner_read` 240/60s user | - | 600 |
| POST | `/api/plan/calendar-feed` | `planner_feed` 10/60s user | - | 600 |
| GET | `/api/plan/export.csv` | `plan_export` 10/60s user | export | 30 |
| GET | `/api/plan/ics/{token}` | `ics_ip` 60/60s ip, `ics_token` 20/60s ip | - | 600 |
| POST | `/api/plan/items` | `planner_write` 60/60s user | - | 600 |
| PATCH | `/api/plan/items/{item_id}` | `planner_write` 60/60s user | - | 600 |
| POST | `/api/plan/items/{item_id}/status` | `plan_write` 30/60s user | - | 600 |
| GET | `/api/plan/preview` | `plan_read` 240/60s user | - | 600 |
| POST | `/api/plan/rebalance` | `planner_rebalance` 6/60s user | - | 600 |
| GET | `/api/plan/rebalances` | `planner_read` 240/60s user | - | 600 |
| GET | `/api/plan/week` | `planner_read` 240/60s user | - | 600 |
| GET | `/api/public-config` | `public_read` 120/60s ip | - | 600 |
| GET | `/api/push/key` | `planner_read` 240/60s user | - | 600 |
| GET | `/api/push/subscriptions` | `planner_read` 240/60s user | - | 600 |
| POST | `/api/push/subscriptions` | `planner_write` 60/60s user | - | 600 |
| DELETE | `/api/push/subscriptions/{subscription_id}` | `planner_write` 60/60s user | - | 600 |
| POST | `/api/push/test` | `push_test` 3/60s user | - | 600 |
| GET | `/api/qbank/pool` | `qbank_read` 300/60s user | - | 600 |
| GET | `/api/reading-position` | `courses_read` 240/60s user | - | 600 |
| PUT | `/api/reading-position` | `position_write` 120/60s user | - | 600 |
| GET | `/api/ready` | `health` 60/60s ip | - | 600 |
| POST | `/api/report-issue` | `report_write` 10/60s user | - | 600 |
| GET | `/api/search` | `search_local` 120/60s user | - | 600 |
| GET | `/api/search/web` | `search_web` 20/60s user | - | 600 |
| GET | `/api/today` | `plan_read` 240/60s user | - | 600 |
| GET | `/api/vault` | `vault_read` 120/60s user | - | 600 |
| POST | `/api/vault/uploads` | `vault_write` 30/60s user | ingest/upload/import | 30 |
| DELETE | `/api/vault/{doc_id}` | `vault_write` 30/60s user | - | 600 |
| PATCH | `/api/vault/{doc_id}` | `vault_write` 30/60s user | - | 600 |
| POST | `/api/vault/{doc_id}/complete` | `vault_write` 30/60s user | - | 600 |
| GET | `/api/vault/{doc_id}/url` | `vault_url` 60/60s user | - | 600 |
| GET | `/api/videos` | `videos_read` 240/60s user | - | 600 |
| GET | `/api/videos/overview` | `videos_read` 240/60s user | - | 600 |
| GET | `/api/videos/topics` | `videos_read` 240/60s user | - | 600 |
| DELETE | `/api/videos/{media_id}/watched` | `videos_write` 120/60s user | - | 600 |
| PUT | `/api/videos/{media_id}/watched` | `videos_write` 120/60s user | - | 600 |

## ASVS 4.0.3 Level 2

One row per ASVS section; a requirement whose status differs from its section's is listed on its own row. Status:
**met** (with where it is implemented or tested), **n/a** (with why), **gap** (with what is missing and its owner).

| ASVS | Topic | Status | Evidence / reason |
|---|---|---|---|
| V1.1 | Secure SDLC | met | Segment plan with mandatory independent review (`docs/PLAN.md`, `docs/REVIEW_CHECKLIST.md`); threat model above |
| V1.2 | Authentication architecture | met | One verifier (`auth.py` `TokenVerifier`) for the API and Aegra (`agent/auth.py`); no service accounts reachable from clients |
| V1.4 | Access control architecture | met | Server-side checks only (`AccountUser`, `AdminUser`); RLS as the second line; deny by default (`alter default privileges ... revoke all`) |
| V1.5 | Input/output architecture | met | Pydantic models with `extra="forbid"` on every request body; output via React or allowlist renderers; contracts generated from OpenAPI |
| V1.6 | Cryptographic architecture | met | Keys only in env (`settings.py`); no custom crypto: Supabase JWT (ES256/RS256), age (pyrage) for backups, SHA-256 token hashes for ICS feeds |
| V1.7 | Logging architecture | met | Structured JSON logs (`logs.py`), central redaction, request ids |
| V1.8 | Data protection architecture | met | Data classes: content (public), study data (owner), vault (private, never to models), backups (encrypted) |
| V1.9 | Communications architecture | met | TLS at HF's proxy and to Supabase/providers; HSTS; `upgrade-insecure-requests` |
| V1.10 | Malicious software architecture | met | Pinned dependencies, audits, SBOM, SHA-pinned Actions |
| V1.11 | Business logic architecture | met | Exam timer on the server clock; attempts and FSRS scheduling server-authoritative |
| V1.12 | File upload architecture | met | Uploads to private buckets, never served from our origin as HTML; ingest sniffs type and caps size |
| V1.14 | Configuration architecture | met | One container, one worker, config in `config/` + `app_config` with history; segregated sandbox documents |
| V2.1 | Password security | n/a | No passwords: sign-in is Supabase magic link or Google OAuth |
| V2.2 | General authenticator security | met | Supabase Auth rate-limits magic-link sends; our auth routes are bucketed (`me_*`); no account enumeration in our responses |
| V2.3 | Authenticator lifecycle | met | Supabase Auth (email confirmation required for admin) |
| V2.4–V2.6 | Credential storage, look-up secrets, out-of-band | n/a | Not stored by Naadi: delegated to Supabase Auth |
| V2.7 | Out-of-band verifiers (magic link) | met | Supabase single-use, expiring links; PKCE callback (`/auth/callback?code=`); e2e `auth-once.spec.ts` |
| V2.8 | One-time verifiers (TOTP) | n/a | Not offered |
| V2.9 | Cryptographic verifiers | n/a | Not offered |
| V2.10 | Service authentication | met | Service keys only on the Space (env), never in the client or CI artifacts (`scan_artifacts`) |
| V3.1 | Session fundamentals | met | Bearer JWT in an `Authorization` header (no cookies, no CSRF surface) |
| V3.2 | Session binding | met | Supabase issues per-sign-in sessions; `sid` checked against `auth.sessions` for account data |
| V3.3 | Session termination | met | Sign-out revokes (`scope=local`/`global`); revoked sessions get 401 (`test_auth*`, `auth-once.spec.ts`) |
| V3.4 | Cookie-based sessions | n/a | No session cookies |
| V3.5 | Token-based sessions | met | JWKS verification, `aud`, `iss`, `exp`/`nbf`/`iat` with ≤ 60 s skew, unknown `kid` refetch rate-limited (`auth.py`) |
| V3.7 | Defences against session exploits | met | Admin decisions re-check the session and confirmed email server-side |
| V4.1 | General access control | met | Every route depends on `get_current_user`/`get_account_user`/`require_admin` or is anonymous by design (audit test lists them) |
| V4.2 | Operation-level access control | met | RLS owner policies (`<t>_<cmd>_own`), client grants explicit per table; IDOR tests in `tests/db/test_rls.py` |
| V4.3 | Administrative interfaces | met | `ADMIN_EMAILS` + confirmed email, server-side; admin UI hidden client-side only as convenience; admin audit log (S19) |
| V5.1 | Input validation | met | Pydantic everywhere, length and range bounds, `extra="forbid"`; 16 KB / per-route body caps |
| V5.2 | Sanitisation and sandboxing | met | SVG sanitiser for figures (`seed/svg.py`); `SafeHtml` allowlist; Mermaid in a sandboxed iframe |
| V5.3 | Output encoding / injection | met | Parameterised SQL (psycopg) everywhere; `S608` lint on; React escapes output |
| V5.4 | Memory, string, unmanaged code | n/a | Python/TypeScript only |
| V5.5 | Deserialisation | met | JSON only; `.apkg` read through SQLite with size caps (Anki importer), archive paths validated in restore |
| V6.1 | Data classification | met | See Privacy and the data classes in V1.8 |
| V6.2 | Algorithms | met | Platform algorithms only (JWT ES256/RS256 via PyJWT, X25519/ChaCha20-Poly1305 via age, SHA-256) |
| V6.3 | Random values | met | `secrets.token_urlsafe` for ICS tokens; UUIDv4 ids; SearXNG secret per start |
| V6.4 | Secret management | met | Space secrets/variables; GitHub holds only `HF_TOKEN`; rotation in RUNBOOK |
| V7.1 | Log content | met | No bodies, tokens or query strings (`logs.py`, `test_errors_and_logging.py`) |
| V7.2 | Log processing | met | Auth decisions logged with reason codes (`auth_rejected`, `admin_denied`), never the token |
| V7.3 | Log protection | met | Logs stay in the Space's log stream (owner-only); secrets scrubbed before writing |
| V7.4 | Error handling | met | One error shape; generic 500 message in production, details only locally (`middleware.py`) |
| V8.1 | General data protection | met | `Cache-Control: no-store` on `/api/*`; vault never cached offline |
| V8.2 | Client-side data protection | met | The device copy holds only the signed-in student's own data and no answer keys, keyed by user id (another account on the same device never reads it; queued writes wait for their owner) |
| V8.3 | Sensitive private data | met | PII never in URLs; export-all for the student (S21); account deletion cascades (`on delete cascade` to `auth.users`) |
| V9.1 | Client communication security | met | TLS 1.2+ at HF's proxy, HSTS |
| V9.2 | Server communication security | met | TLS to Supabase (`sslmode`), providers over HTTPS only; SearXNG on loopback |
| V10.1 | Code integrity | met | Lockfiles, hash-locked install, SBOM |
| V10.2 | Malicious code search | met | Review per segment; `pnpm audit` / `pip-audit` in CI |
| V10.3 | Application integrity | met | Deploy only from green `main` (`deploy.yml`); no auto-update of code at run time |
| V11.1 | Business logic | met | Exam timing server-side; limits on attempts/blocks; one open diagnostic per student; coach transitions guarded |
| V12.1 | File upload | met | Size caps before reading (Anki 100 MB, ingest per kind, images 25 MB); quota per user via buckets |
| V12.2 | File integrity | met | Type sniffed from content, not the extension (ingest) |
| V12.3 | File execution | met | Uploaded files never executed or served as HTML from our origin; private buckets with signed URLs |
| V12.4 | File storage | met | Supabase Storage, per-bucket policies (five `storage.objects` policies, live_check) |
| V12.5 | File download | met | Signed, expiring URLs for private objects |
| V12.6 | SSRF | met | Allowlisted provider hosts; `pathway_verify` restricted to the official domain with redirect checks; links ingested through Tavily extract; YouTube via the Data API only; SearXNG on loopback |
| V13.1 | Generic web service | met | Same-origin JSON API; OpenAPI exported; no docs UI in production |
| V13.2 | RESTful services | met | Methods enforced (405 with `Allow`); JSON content types; CORS closed (`allow_origins: []`) |
| V13.3 | SOAP | n/a | None |
| V13.4 | GraphQL | n/a | None (pg_graphql off) |
| V14.1 | Build and deploy | met | Reproducible Docker build from tracked files only, context scanned for secrets (`deploy_space.py`) |
| V14.2 | Dependencies | met | Exact pins (versions at least two weeks old when added); audits on every push; SBOM |
| V14.3 | Unintended disclosure | met | No stack traces or debug in production; Prometheus endpoint off; server header only |
| V14.4 | HTTP security headers | met | See Headers |
| V14.5 | HTTP request header validation | met | Request ids validated (`_VALID_RID`); host-agnostic; proxy headers trusted only for the scheme/peer, rate keys use the proxy-appended entry |
| V3.5.2 / V14.4.3 | Trusted Types enforcement | gap | Report-only today. Owner seg22, enforce after one release with an empty report log (rollout above) |
| V1.10.1 | Source control: signed commits | gap | Commits are not signed. Owner: repository owner (decide whether to require signed commits on `main`) |
| V8.3.4 | Data inventory with retention per field | met | Retention per table in ARCHITECTURE "Budgets" and `naadi_api/ops/prune.py` |
| V10.3.2 | Sub-resource integrity | n/a | No third-party scripts or styles are loaded by the app |

## Reporting a vulnerability

Open a private security advisory on the repository (GitHub → Security → Advisories). Do not open a public issue.
