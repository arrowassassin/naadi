# Naadi build plan

Spec: [`handoff/HANDOFF.md`](../handoff/HANDOFF.md). Working rules: [`CLAUDE.md`](../CLAUDE.md). Progress log: [`docs/PROGRESS.md`](PROGRESS.md).

## Decisions that shape the plan

| Decision | Choice | Why |
|---|---|---|
| Hosting | **Docker Space** `arrowassassin/naadi` on free `cpu-basic`, private for now (`deploy/space/`, Python 3.13) | `hf whoami` shows PRO, so Docker is allowed. Same origin for SPA + API; no Gradio workaround |
| Database / auth / files | Supabase Free only (Postgres 17 + pgvector, Auth, Storage). No R2 | One user; Storage's 1 GB is enough. R2 stays an optional later add-on |
| Sign-in | Google OAuth + email magic link (Supabase Auth). No Apple | Owner's choice; both free |
| SDKs | Official libraries: `supabase` / `supabase-js`, `openrouter` + `langchain-openrouter`, `tavily-python`, `huggingface_hub`, `langgraph` | Owner's rule. Raw HTTPS only where no SDK method exists (Supabase auth-settings/JWKS, Management API, Tavily usage) |
| Agent | `langgraph` (Python) inside FastAPI, `PostgresSaver` checkpoints, our own SSE endpoint | The LangGraph Agent Server (which `langgraph-sdk` talks to) is Elastic-2.0 + license key (HANDOFF §3) |
| Contracts | **Pydantic is the source of truth** → OpenAPI 3.1 (`packages/shared/openapi.json`, committed) → generated zod schemas + typed TS client in `packages/shared`. CI fails on drift | FastAPI validates every request/response server-side; the client validates every response with zod |
| Database access from the build sandbox | Raw Postgres can't leave the cloud sandbox (no DNS/TCP). Migrations, seed and DB-backed integration/e2e tests run in **GitHub Actions** and against the **deployed Space**; the sandbox verifies via the Supabase Management API (HTTPS) | Never mocked |
| Bundle layout | `data/ config/ design/ db/ tools/ deploy/` at the repo root (moved from `handoff/`); `handoff/` keeps the spec and research | Matches `CLAUDE.md` paths (`--data ../../data`, `../../tools/check_models.py`) |
| Versions | Exact pins from HANDOFF §3 (all verified on npm/PyPI 2026-11-16). Node 24 LTS, pnpm 12.21.0, TypeScript 7.0.2 (5.9 fallback if a tool breaks) | Spec |
| Test matrix | Playwright WebKit + Chromium + Firefox at 820×1180, 1180×820, 390×844, 412×915, 1440×900, 1920×1080 + axe (WCAG 2.2 AA). Real-device checks in [`DEVICE_CHECKLIST.md`](DEVICE_CHECKLIST.md) | Owner's brief |

## Process per segment

1. **Brief** `docs/segments/NN-name.md`: scope, acceptance criteria (from HANDOFF §8/§11), tests, screens × states, implementer + reason.
2. **Implement** with the chosen subagent (`implementer-sonnet` for bounded pattern work, `implementer-opus` for complex/high-risk; Opus when unsure).
3. **Tests:** vitest + pytest units; integration against the real Supabase project (in CI); Playwright e2e matrix + axe. All green.
4. **Renders:** `ui-verifier` screenshots every changed screen × state (populated, loading, empty, error, offline) × viewports, compared with `design/*.dc.html`.
5. **Flows:** the segment's user flows scripted and run against the deployed Space.
6. **Review:** `reviewer-opus` until **APPROVED** (zero failing/skipped tests, zero console errors, verified screenshots).
7. **Ship:** conventional commits → PR per segment (brief, test results, screenshots) → merge → CI deploys → live smoke.
8. **Log** in `docs/PROGRESS.md`.

**Whole-app review** by `reviewer-opus` after every 3 segments and before each milestone is declared done (full regression e2e, all flows, all screens).

## Segments

| # | Milestone | Segment | Implementer | Why that model |
|---|---|---|---|---|
| 01 | M0 | Monorepo scaffold, tokens + core components, contracts pipeline, health/ready, logging, CI, Docker Space deploy | **Opus** | Foundation every later segment inherits: contracts direction, deploy, CI, security headers |
| 02 | M1 | Alembic migrations (schema + RLS + storage buckets/policies), migration test, RLS test suite | **Opus** | Auth/RLS is high-risk |
| 03 | M1 | Auth (Google + magic link), JWT via JWKS, profiles, admin role gate, app shell (nav pill, phone tab bar, layouts) | **Opus** | Auth + security |
| — | | **Whole-app review 1** | reviewer-opus | |
| 04 | M1 | Seed importer (`seed.py`: Storage upload, HTML→TipTap, figure nodes, all tables, `app_config`), dry-run + verification report; `app_config` API with 60 s cache | **Opus** | Named high-risk (importer) |
| 05 | M1 | Courses map + Reader (`ContentRenderer`, figures, reading position sync, "Report an issue") | **Opus** | Named high-risk (renderer) |
| 06 | M1 | Local search: fastembed embeddings job, hybrid pgvector + tsvector ranking, Search screen (⌘K) | Sonnet | Bounded; design decided by 04/05 |
| — | | **Whole-app review 2 → M1 done** | reviewer-opus | |
| 07 | M2 | Qbank builder + tutor mode + attempts + answer-leak protection | Sonnet | CRUD + UI from patterns; leak rules are explicit |
| 08 | M2 | Timed/exam modes, timer integrity (±1 s across backgrounding), navigator, flags, lab values, Results analytics | **Opus** | Named high-risk (exam timer) |
| 09 | M2 | Cards: FSRS (ts-fsrs 6) review, optimizer-ready logs, keyboard 1–4 | **Opus** | Named high-risk (FSRS) |
| — | | **Whole-app review 3** | reviewer-opus | |
| 10 | M2 | Onboarding (profile, plan generator reproducing the 60-day CSV, 40-question diagnostic) + Today dashboard | **Opus** | Generator correctness against a reference output |
| 11 | M3 | Ink layer (Pointer Events, perfect-freehand, palm rejection, coalesced/predicted events, tools) + latency benchmark harness | **Opus** | Named high-risk (ink/Pencil latency) |
| 12 | M3 | Notes, three-colour highlights, selection toolbar, notebooks with paper templates, PDF export with ink | Sonnet | Builds on 11's ink API |
| — | | **Whole-app review 4 → M2 + M3 done** | reviewer-opus | |
| 13 | M4 | LLM layer: `llm.py` (OpenRouter SDK, fallbacks, rpm bucket, daily cap, `llm_usage`, PII stripping), Tavily guard (`search_cache`, credit guard), `check_models.py` on the SDK, degradation UI | **Opus** | Budget/privacy guards are security-critical |
| 14 | M4 | Tutor graph (guard → tutor ⇄ tools → compose), SSE, page context, assistant bar/sheet + answer blocks, confirm interrupts, exam silence | **Opus** | Named high-risk (LangGraph + SSE) |
| 15 | M4 | Uploads → private chapters (PyMuPDF, vision fallback), Library, handwriting recognition job, `tools/model_eval` (30 prompts + 100 held-out questions) | **Opus** | Untrusted file handling + vision |
| — | | **Whole-app review 5 → M4 done** | reviewer-opus | |
| 16 | M5 | Planner screen: week calendar, rebalance, ICS feed, Web Push | Sonnet | Generator exists from 10; bounded |
| 17 | M5 | Pathway: stages/tasks, re-verify badges, vault (private Storage, never to LLMs), cost tracker | Sonnet | CRUD on seeded data; vault policies from 02 |
| 18 | M5 | Offline week: PWA, "download this week", Dexie cache, outbox sync, conflict handling (LWW per row, ink by stroke id) | **Opus** | Named high-risk (offline sync) |
| — | | **Whole-app review 6 → M5 done** | reviewer-opus | |
| 19 | Admin | Admin console: TipTap chapter editor, figure/PDF/YouTube/question CRUD, `app_config` editor with history, coverage heat-map | **Opus** | Named high-risk (admin editor) |
| 20 | M6 | YouTube mapper + approvals + IFrame player with timestamped notes; Anki `.apkg` import/export | Sonnet | Spec'd tools exist; bounded |
| 21 | M6 | Authoring pipeline (draft → lint → blind solve → 2 reviewers → publish → psychometrics) + reviewer UI; Settings + export-all | **Opus** | Multi-model pipeline with quality gates |
| — | | **Whole-app review 7 → M6 done** | reviewer-opus | |
| 22 | Ops | Hardening: nightly `pg_dump` → private HF dataset + tested restore, weekly YouTube refresh, rate limits audit, CSP, ASVS L2 checklist, Lighthouse PWA ≥ 90, perf budgets, RUNBOOK / ARCHITECTURE / CONTENT_GUIDE / SECURITY | **Opus** | Security + recovery |
| — | | **Final full-app audit** | reviewer-opus | |

## Acceptance tests per segment

Each brief refines these. "e2e" = the full Playwright matrix + axe.

| # | Must pass |
|---|---|
| 01 | `pnpm lint`, `pnpm typecheck`, `pnpm test`, `uv run pytest` green; contract drift check; e2e: `/` renders the token sheet + live health panel with zero axe violations and zero console errors; `/api/health` reports DB, storage, OpenRouter quota, Tavily credits; `/api/ready`; JSON logs carry `request_id`; CSP + security headers present; CI green; live Space serves the token sheet and a healthy `/api/health` |
| 02 | Migration test: empty → upgrade head → seed smoke → downgrade one → upgrade; RLS suite: user B cannot read/write any of user A's rows in all 13 user tables, anon reads only published shared content; buckets and storage policies exist |
| 03 | Magic-link and Google sign-in flows (Google via Supabase test path + manual device check); JWT verified via JWKS; expired/forged tokens rejected; admin routes return 403 to non-admins server-side; shell layouts at all viewports |
| 04 | `seed.py --dry-run` changes nothing and prints the plan; real run imports 14 chapters, 259 sections, 685 questions, 95 figures, 217 taxonomy nodes, the question map, 16 stages, 186 tasks, plan template, `app_config`; second run is a no-op; verification report matches counts; no `[[figure:` left in any body |
| 05 | Every chapter browsable; figures render from Storage; unknown node shows an error card in dev and logs in prod; reading position syncs across two browsers; filters by exam/system; screens × states verified |
| 06 | Hybrid search returns chapters/questions/notes; local results < 300 ms p95 on seeded data; ⌘K; answers never leaked through search snippets |
| 07 | Build a block by system/discipline/status; tutor mode reveals only after answering; attempts persisted; API never returns `answer`/explanations for unanswered questions |
| 08 | Mock A runs as timed 2×40; timer ±1 s across tab backgrounding and reload; exam mode hides explanations; Results: % correct, time/question, weak areas (amber + word), official weight overlay |
| 09 | ts-fsrs 6 defaults; Again/Hard/Good/Easy + keys 1–4; review logs optimizer-ready; due counts correct across timezones |
| 10 | Generator reproduces `data/study_plan_step1_60d.csv` for the reference inputs; onboarding ≤ 3 min path; diagnostic is a 40-question mixed block; Today: one lime CTA, real numbers, < 1.5 s warm |
| 11 | Pen draws, touch scrolls, palm rejection, coalesced + predicted events, strokes stored `[x,y,p,t]` surface-relative, survives reflow/zoom; 5k-stroke page scrolls at 60 fps; latency harness recorded |
| 12 | Highlights lime/violet/amber with words; selection toolbar Highlight/Note/Card/Ask; notebooks with 5 paper types; filter by colour/anchor; PDF export includes ink |
| 13 | Only `:free` IDs pass validation; rpm bucket 18/min; daily cap with 70 % chat reserve; ≤ 4 calls/turn; PII stripped (tests); Tavily cache hit/miss + 950-credit stop; degradation visible in UI |
| 14 | Streams tokens/blocks/citations over SSE through the Space proxy; page-aware; write tools need confirmation; exam mode silent; never leaks answers before answering (eval) |
| 15 | 50 MB PDF → readable private chapter < 2 min; RLS-private; searchable; vault never sent to LLMs; 30-prompt eval ≥ rubric bar |
| 16 | Rebalance moves missed items forward without breaking assessment days; ICS URL subscribes; Web Push delivered on installed PWA |
| 17 | Every task shows `last_verified` + official link; > 90-day "re-verify" badge; vault private (RLS + signed URLs); costs total by stage |
| 18 | Airplane-mode study day end to end; outbox replays; LWW per row; ink merged by stroke id; conflict tests |
| 19 | Admin adds a chapter, PDF, figure, YouTube video and question; each appears live for the student with no redeploy; `app_config` edit + history + 60 s cache |
| 20 | Mapper lists channel uploads, keeps embeddable, writes candidates; approvals; IFrame player unmodified; Anki import/export round-trips |
| 21 | Lint rules from `authoring_rules.md`; reviewer from a different family; disagreement flags; 2 human approvals to publish; psychometrics after 30 responses |
| 22 | Backup restores into a scratch project (tested once); CSP strict; dependency audit in CI; Lighthouse PWA ≥ 90; Today interactive < 2 s on throttled 4G iPad profile; all docs written |

## Known constraints and risks

1. **No raw Postgres from the build sandbox:** DB-backed tests run in GitHub Actions and on the Space. **GitHub Actions secrets must be added by the owner** if this session can't write them (list in [`SECRETS.md`](SECRETS.md)).
2. **Free model churn:** `check_models.py` runs in CI; daily cap is 50 until the one-time $10 purchase, which limits tutor testing.
3. **Private Space:** smoke/e2e against the live URL send the HF token; PWA install on iPad needs a public Space or custom domain. Decide before M5.
4. **Supabase Free pauses after 7 idle days:** daily health ping from CI (segment 01 adds the cron).
5. **Real iPad checks** (Pencil latency, install, offline) are manual per milestone.
