# Naadi build plan

Spec: [`handoff/HANDOFF.md`](../handoff/HANDOFF.md). Working rules: [`CLAUDE.md`](../CLAUDE.md). Progress log: [`docs/PROGRESS.md`](PROGRESS.md).

## Decisions that shape the plan

| Decision | Choice | Why |
|---|---|---|
| Hosting | **Docker Space** `arrowassassin/naadi` on free `cpu-basic`, private for now (`deploy/space/`, Python 3.13) | `hf whoami` shows PRO, so Docker is allowed. Same origin for SPA + API; no Gradio workaround |
| Database / auth / files | Supabase Free only (Postgres 17 + pgvector, Auth, Storage). No R2 | One user; Storage's 1 GB is enough. R2 stays an optional later add-on |
| Sign-in | Google OAuth + email magic link (Supabase Auth). No Apple | Owner's choice; both free |
| SDKs | Official libraries: `supabase` / `supabase-js`, `openrouter` + `langchain-openrouter`, `tavily-python`, `huggingface_hub`, `langgraph` | Owner's rule. Raw HTTPS only where no SDK method exists (Supabase auth-settings/JWKS, Management API, Tavily usage) |
| Agent platform | **Aegra** (`aegra-api`, Apache-2.0): a self-hosted, LangGraph-SDK-compatible Agent Protocol server (assistants, threads, background + streaming runs, crons, human-in-the-loop interrupts, store) running **every** Naadi agent graph (`langgraph`). It is the top-level process in the Space and hosts our FastAPI app via `aegra.json` → `http.app`. Postgres = Supabase (`DATABASE_URL`); in-memory broker (single container, no Redis). Custom auth = Supabase JWT, threads owned per user. Clients: `langgraph-sdk` (py) in the backend/jobs, `@langchain/langgraph-sdk` (`useStream`) in the web | Owner wants a full agentic flow, free. LangChain's own Agent Server needs a production licence key; `langgraph dev` is dev-only |
| Contracts | **Pydantic is the source of truth** → OpenAPI 3.1 (`packages/shared/openapi.json`, committed) → generated zod schemas + typed TS client in `packages/shared`. CI fails on drift | FastAPI validates every request/response server-side; the client validates every response with zod |
| Database access from the build sandbox | Raw Postgres can't leave the cloud sandbox (no DNS/TCP). Migrations, seed and DB-backed integration/e2e tests run in **GitHub Actions** and against the **deployed Space**; the sandbox verifies via the Supabase Management API (HTTPS) | Never mocked |
| Agent graphs | `tutor` (page-aware chat, tools, confirm-interrupts, exam silence) · `ingest` (upload → extract → vision fallback → chunk → embed → private chapter, background run) · `ink_ocr` (handwriting → `recognized_text`, background) · `coach` (nightly weak-area analysis → Today suggestions, cron) · `planner` (rebalance proposals, interrupt to confirm) · `pathway_verify` (weekly official-domain re-check, flags stale tasks, cron) · `author` (draft → lint → blind review by a different family → human queue) · `youtube_map` (weekly candidates, cron). All LLM calls go through `naadi_api/llm.py` budget guards; jobs share the 30 % non-chat reserve | One runtime, one auth model, one budget |
| Alembic | Naadi's migrations use version table `naadi_alembic_version`; Aegra keeps its own `alembic_version` and tables | Both ship Alembic migrations into the same database |
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
| 02 | M1 | Alembic migrations (schema + RLS + storage buckets/policies, `naadi_alembic_version`), migration test, RLS test suite | **Opus** | Auth/RLS is high-risk |
| 03 | M1 | Auth (Google + magic link), JWT via JWKS, profiles, admin role gate, app shell (nav pill, phone tab bar, layouts) | **Opus** | Auth + security |
| — | | **Whole-app review 1** | reviewer-opus | |
| 04 | M4 (pulled forward) | **Agent platform:** `llm.py` (OpenRouter SDK + `langchain-openrouter`, free-only fallbacks, 18 rpm bucket, daily cap with 70 % chat reserve, ≤ 4 calls/turn, `llm_usage`, PII stripping), Tavily guard (`search_cache`, 950-credit stop), `check_models.py` on the SDK; **Aegra host** (top-level ASGI hosting `naadi_api`, Supabase-JWT auth handler, per-user thread/run ownership, graph registry, Postgres checkpoints, crons, run cancellation), agent run status in `/api/health`, degradation UI; first graph `ping` proving stream + interrupt + background run + cron end to end on the Space | **Opus** | Security- and budget-critical runtime every later agent depends on |
| 05 | M1 | Seed importer (`seed.py`: Storage upload, HTML→TipTap, figure nodes, all tables, `app_config`), dry-run + verification report; `app_config` API with 60 s cache | **Opus** | Named high-risk (importer) |
| 06 | M1 | Courses map + Reader (`ContentRenderer`, figures, reading position sync, "Report an issue") | **Opus** | Named high-risk (renderer) |
| — | | **Whole-app review 2** | reviewer-opus | |
| 07 | M1 | Local search: fastembed embeddings job, hybrid pgvector + tsvector ranking, Search screen (⌘K) → **M1 done** | Sonnet | Bounded; design decided by 05/06 |
| 08 | M2 | Qbank builder + tutor mode + attempts + answer-leak protection | Sonnet | CRUD + UI from patterns; leak rules are explicit |
| 09 | M2 | Timed/exam modes, timer integrity (±1 s across backgrounding), navigator, flags, lab values, Results analytics | **Opus** | Named high-risk (exam timer) |
| — | | **Whole-app review 3** | reviewer-opus | |
| 10 | M2 | Cards: FSRS (ts-fsrs 6) review, optimizer-ready logs, keyboard 1–4 | **Opus** | Named high-risk (FSRS) |
| 11 | M2 | Onboarding (profile, plan generator reproducing the 60-day CSV, 40-question diagnostic) + Today dashboard + `coach` graph (nightly cron) → **M2 done** | **Opus** | Generator correctness against a reference output + first cron agent |
| 12 | M3 | Ink layer (Pointer Events, perfect-freehand, palm rejection, coalesced/predicted events, tools) + latency benchmark harness | **Opus** | Named high-risk (ink/Pencil latency) |
| — | | **Whole-app review 4** | reviewer-opus | |
| 13 | M3 | Notes, three-colour highlights, selection toolbar, notebooks with paper templates, PDF export with ink, `ink_ocr` graph (handwriting search) → **M3 done** | Sonnet | Builds on 12's ink API and 04's runtime |
| 14 | M4 | `tutor` graph (guard → tutor ⇄ tools → compose), streaming via `useStream`, page context, assistant bar/sheet + answer blocks, confirm interrupts, exam silence | **Opus** | Named high-risk (agent + streaming) |
| 15 | M4 | `ingest` graph: uploads → private chapters (PyMuPDF, vision fallback), Library; `tools/model_eval` (30 prompts + 100 held-out questions) → **M4 done** | **Opus** | Untrusted file handling + vision |
| — | | **Whole-app review 5** | reviewer-opus | |
| 16 | M5 | Planner screen: week calendar, `planner` graph rebalance (interrupt to confirm), ICS feed, Web Push | Sonnet | Generator exists from 11; bounded |
| 17 | M5 | Pathway: stages/tasks, re-verify badges, vault (private Storage, never to LLMs), cost tracker, `pathway_verify` weekly cron graph | Sonnet | CRUD on seeded data; vault policies from 02 |
| 18 | M5 | Offline week: PWA, "download this week", Dexie cache, outbox sync, conflict handling (LWW per row, ink by stroke id) → **M5 done** | **Opus** | Named high-risk (offline sync) |
| — | | **Whole-app review 6** | reviewer-opus | |
| 19 | Admin | Admin console: TipTap chapter editor, figure/PDF/YouTube/question CRUD, `app_config` editor with history, coverage heat-map, agent runs/crons view | **Opus** | Named high-risk (admin editor) |
| 20 | M6 | `youtube_map` cron graph + approvals + IFrame player with timestamped notes; Anki `.apkg` import/export | Sonnet | Spec'd tools exist; bounded |
| 21 | M6 | `author` graph (draft → lint → blind solve → 2 human reviewers → publish → psychometrics) + reviewer UI; Settings + export-all → **M6 done** | **Opus** | Multi-model pipeline with quality gates |
| — | | **Whole-app review 7** | reviewer-opus | |
| 22 | Ops | Hardening: nightly `pg_dump` → private HF dataset + tested restore, rate limits audit, CSP, ASVS L2 checklist, Lighthouse PWA ≥ 90, perf budgets, RUNBOOK / ARCHITECTURE / CONTENT_GUIDE / SECURITY | **Opus** | Security + recovery |
| — | | **Final full-app audit** | reviewer-opus | |

## Acceptance tests per segment

Each brief refines these. "e2e" = the full Playwright matrix + axe.

| # | Must pass |
|---|---|
| 01 | `pnpm lint`, `pnpm typecheck`, `pnpm test`, `uv run pytest` green; contract drift check; e2e: `/` renders the token sheet + live health panel with zero axe violations and zero console errors; `/api/health` reports DB, storage, OpenRouter quota, Tavily credits; `/api/ready`; JSON logs carry `request_id`; CSP + security headers present; CI green; live Space serves the token sheet and a healthy `/api/health` |
| 02 | Migration test: empty → upgrade head → seed smoke → downgrade one → upgrade; RLS suite: user B cannot read/write any of user A's rows in all 13 user tables, anon reads only published shared content; buckets and storage policies exist; `naadi_alembic_version` used |
| 03 | Magic-link and Google sign-in flows (Google via Supabase + manual device check); JWT verified via JWKS; expired/forged tokens rejected; admin routes 403 to non-admins server-side; shell layouts at all viewports |
| 04 | Only `:free` IDs pass validation; 18 rpm bucket; daily cap with 70 % chat reserve; ≤ 4 LLM calls/turn; PII stripped (tests); Tavily cache hit/miss + 950-credit stop; Aegra hosts `naadi_api` on one origin; user A cannot see/stream/cancel user B's threads or runs (tests); `ping` graph streams tokens, raises and resumes an interrupt, completes a background run and a cron run on the live Space; degradation visible in UI |
| 05 | `seed.py --dry-run` changes nothing and prints the plan; real run imports 14 chapters, 259 sections, 685 questions, 95 figures, 217 taxonomy nodes, the question map, 16 stages, 186 tasks, plan template, `app_config`; second run is a no-op; verification report matches counts; no `[[figure:` left |
| 06 | Every chapter browsable; figures render from Storage; unknown node shows an error card in dev and logs in prod; reading position syncs across two browsers; filters by exam/system; screens × states verified |
| 07 | Hybrid search returns chapters/questions/notes; local results < 300 ms p95 on seeded data; ⌘K; answers never leaked through snippets |
| 08 | Build a block by system/discipline/status; tutor mode reveals only after answering; attempts persisted; API never returns `answer`/explanations for unanswered questions |
| 09 | Mock A runs as timed 2×40; timer ±1 s across backgrounding and reload; exam mode hides explanations; Results: % correct, time/question, weak areas (amber + word), official weight overlay |
| 10 | ts-fsrs 6 defaults; Again/Hard/Good/Easy + keys 1–4; review logs optimizer-ready; due counts correct across timezones |
| 11 | Generator reproduces `data/study_plan_step1_60d.csv` for the reference inputs; onboarding ≤ 3 min; 40-question mixed diagnostic; Today: one lime CTA, real numbers, < 1.5 s warm; `coach` cron writes suggestions within budget |
| 12 | Pen draws, touch scrolls, palm rejection, coalesced + predicted events, strokes `[x,y,p,t]` surface-relative, survive reflow/zoom; 5k-stroke page at 60 fps; latency harness recorded |
| 13 | Highlights lime/violet/amber with words; selection toolbar Highlight/Note/Card/Ask; 5 paper types; filter by colour/anchor; PDF export includes ink; `ink_ocr` fills `recognized_text` and handwriting is searchable |
| 14 | Streams tokens/blocks/citations through the Space proxy via `useStream`; page-aware; write tools need confirmation (interrupt); exam mode silent; never leaks answers before answering (eval) |
| 15 | 50 MB PDF → readable private chapter < 2 min via the `ingest` background run; RLS-private; searchable; vault never sent to LLMs; 30-prompt eval ≥ rubric bar |
| 16 | `planner` rebalance moves missed items forward without breaking assessment days, only after confirmation; ICS URL subscribes; Web Push delivered on installed PWA |
| 17 | Every task shows `last_verified` + official link; > 90-day "re-verify" badge; vault private (RLS + signed URLs); costs total by stage; `pathway_verify` flags stale tasks with official sources |
| 18 | Airplane-mode study day end to end; outbox replays; LWW per row; ink merged by stroke id; conflict tests |
| 19 | Admin adds a chapter, PDF, figure, YouTube video and question; each appears live with no redeploy; `app_config` edit + history + 60 s cache; agent runs/crons visible |
| 20 | Mapper lists channel uploads, keeps embeddable, writes candidates; approvals; IFrame player unmodified; Anki import/export round-trips |
| 21 | Lint rules from `authoring_rules.md`; reviewer from a different family; disagreement flags; 2 human approvals to publish; psychometrics after 30 responses |
| 22 | Backup restores into a scratch project (tested once); CSP strict; dependency audit in CI; Lighthouse PWA ≥ 90; Today interactive < 2 s on throttled 4G iPad profile; all docs written |

## Known constraints and risks

1. **No raw Postgres from the build sandbox:** DB-backed tests run in GitHub Actions and on the Space. **GitHub Actions secrets must be added by the owner** if this session can't write them (list in [`SECRETS.md`](SECRETS.md)).
2. **Free model churn:** `check_models.py` runs in CI; daily cap is 50 until the one-time $10 purchase, which limits tutor testing.
3. **Private Space:** smoke/e2e against the live URL send the HF token; PWA install on iPad needs a public Space or custom domain. Decide before M5.
4. **Supabase Free pauses after 7 idle days; free `cpu-basic` Spaces sleep after 48 h idle:** the daily GitHub Actions health ping keeps both awake, which also keeps Aegra's crons firing.
6. **Aegra maturity:** pre-1.0 (0.11.0, monthly releases). Pin exactly; segment 04 includes an upgrade test. Fallback if it blocks us: run the same graphs in-process with `langgraph` + `PostgresSaver` (graphs are unchanged; only the host differs).
5. **Real iPad checks** (Pencil latency, install, offline) are manual per milestone.
