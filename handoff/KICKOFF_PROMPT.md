You are the lead engineer building **Naadi**, an iPad-first USMLE study and career platform for an Indian MBBS graduate, from the handoff bundle in this folder. Build it to production quality, end to end. No mockups, no placeholders, no fake data, no TODO stubs, no "coming soon" screens.

## 0. Read first (in this order) and confirm your understanding in a short plan
1. `README.md` (bundle map)
2. `HANDOFF.md` (the spec; it overrides your defaults)
3. `CLAUDE.md` (working rules); copy it to the new repo root
4. `design/README.md`, then `design/System.dc.html` (tokens/components), then each board you implement
5. `db/schema.sql`, `config/*.yaml`, `config/authoring_rules.md`, `data/pathway/README.md`
6. Copy `claude-setup/.claude/agents/*` into the repo's `.claude/agents/`. Those subagents are mandatory (see §3).

Then write `docs/PLAN.md`: segments, the order, which implementer model per segment and why, and the acceptance tests per segment. Start M0 immediately after.

## 0.5 Environment file first: create it, then pause for me
Before writing any app code, create the env files and then **stop and ask me to fill them in**. Continue only after I confirm. Then verify every value with a script (`scripts/check_env.py`): DB connect plus `select 1`, Supabase auth settings reachable, OpenRouter `GET /api/v1/key`, a Tavily test search (1 credit), and `hf auth whoami`. Report pass/fail per key, never printing the values.
- `.env.example` (committed): every variable, empty, each with a comment saying where to get it and whether it's a secret or a public variable.
- `.env` (gitignored; add `.env*` except `.env.example` to `.gitignore` first), the same keys:
  - **Supabase:**
    - `DATABASE_URL` (Session pooler, port 5432, `sslmode=require`)
    - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
    - `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (public)
  - **AI:** `OPENROUTER_API_KEY`, `OPENROUTER_DAILY_CAP` (50 or 1000), `OPENROUTER_APP_URL`, `OPENROUTER_APP_TITLE`, `TAVILY_API_KEY`
  - **Hugging Face:** `HF_TOKEN` (write), `HF_SPACE_ID` (e.g. user/naadi), `HF_BACKUP_DATASET` (e.g. user/naadi-backups, private)
  - **Auth/admin:** `ADMIN_EMAILS`, `GOOGLE_OAUTH_CONFIGURED` (true once Google is enabled in Supabase; the client ID and secret live only in Supabase)
  - **Optional, later:** `YOUTUBE_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`
  - **App:** `APP_ENV` (local|staging|prod), `APP_BASE_URL`, `LOG_LEVEL`

Also write `docs/SECRETS.md`, a table mapping each key to its source, its Hugging Face Space secret or variable name, and its GitHub Actions secret name. Add a `scripts/push_space_secrets.sh` that runs `hf spaces secrets set` / `hf spaces variables add` from `.env` after asking me to confirm.

## 1. Non-negotiables
- **Nothing hardcoded in the app.** `apps/web` ships zero content and zero business config. Every screen renders from the API, which reads **Supabase Postgres** (chapters, sections as TipTap JSON, questions, flashcards, taxonomy, pathway, media metadata, `app_config`) and **Supabase Storage** (figures, PDFs, images, uploads, vault). Own large videos go in **Cloudflare R2**; YouTube videos are `media_assets` rows rendered with the official IFrame player.
- Model IDs, budgets, search rules, feature flags, exam timing rules and editable UI copy live in the `app_config` table. Seed it from `config/*.yaml` and expose it in an admin console with history. A content or config change must never need a redeploy.
- **Seed via the importer only.** `apps/api/scripts/seed.py` uploads `data/figures/*.svg` to Storage, converts section HTML to TipTap JSON, rewrites `[[figure:ID]]` into figure nodes, and upserts chapters, questions, taxonomy, the question→taxonomy map, pathway stages and tasks, the 60-day plan template and `app_config`. It's idempotent, and has a `--dry-run` and a verification report.
- **Free stack** exactly as HANDOFF specifies:
  - Hosting: Hugging Face Space, Gradio SDK + `gradio.Server` on the free ZeroGPU flavor (`deploy/free-space/`); Docker path only if `hf auth whoami` shows PRO.
  - Data: Supabase Free.
  - Models: OpenRouter `:free` models only, via `config/models.yaml` → `app_config.models`.
  - Embeddings: local fastembed bge-small (384-dim halfvec).
  - Search: Tavily free, cached and budget-guarded.
  - Agent: LangGraph Python.
  - Latest stable library versions, pinned as in HANDOFF §3.
- **Auth:** Supabase Auth with Google OAuth and email magic link. RLS on every user table, tested.
- **Secrets** already exist as Hugging Face Space secrets. Read them via `naadi_api/settings.py` using the names in HANDOFF §9. If a name differs, add an alias; never ask me to paste secrets into code. If a required secret or service is missing, stop that segment and tell me exactly what to create (e.g., the Supabase project, the Google OAuth client, the one-time $10 OpenRouter credit purchase).
- **All devices, iPad first.** Design and polish for the iPad first (Safari, PWA, Apple Pencil ink), but every feature must be fully usable on **phones** (iPhone Safari, Android Chrome) and **laptops/desktops** (Safari, Chrome, Edge, Firefox on macOS/Windows). Use the phone layouts in `design/Phone*.dc.html` (bottom tab bar, full-screen assistant sheet) and the laptop boards; layouts are fluid between breakpoints (phone < 640, tablet 640–1100, laptop > 1100), not three fixed sizes. Input parity: Pencil, finger, mouse/trackpad and keyboard shortcuts all work; ink falls back to finger/mouse drawing where there's no pen. No feature is iPad-only. Every UI segment must pass on all four test viewports plus a 1920×1080 desktop.
- **Medical-content safety:** never reveal an answer before the student answers; the assistant is silent in exam mode; strip PII before any LLM call; never send vault files to an LLM; follow the content license gates in `config/sources.yaml`.

## 2. Enterprise-ready means
- Typed contracts end to end: zod schemas in `packages/shared` generate the pydantic models (or the reverse), plus an OpenAPI schema with a generated TS client. Validate every request and response.
- Alembic migrations with RLS policies in the same migration, plus a migration test (upgrade from empty → seed → downgrade one).
- Structured JSON logging with request IDs. Error tracking (self-hosted-free option or log-based). `/api/health` (DB, storage, OpenRouter key quota via `GET /api/v1/key`, Tavily credit guard) and `/api/ready`.
- Rate limiting, the LLM budget guard (`llm_usage`) and the search guard (`search_cache`), with graceful degradation paths that are visible in the UI.
- Accessibility: WCAG 2.2 AA (axe-core in CI, keyboard paths, focus rings, reduced motion).
- Performance budgets: Today screen interactive under 2 s on iPad over 4G. Pencil latency benchmark recorded (HANDOFF §5.3). Lighthouse PWA ≥ 90.
- Offline: "download this week", an outbox sync queue, and conflict handling, all tested.
- Security: OWASP ASVS L2 checklist in `docs/SECURITY.md`; dependency audit in CI; CSP headers; signed URLs for private files; admin routes role-gated server-side.
- Ops:
  - GitHub Actions CI: lint, type-check, unit, integration, e2e, `tools/check_models.py`, build, deploy to the Space.
  - Daily cron: health ping (keeps Supabase awake) and `pg_dump` to a private HF dataset.
  - Weekly cron: `youtube_mapper.py --refresh`.
- Docs: `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md` (deploy, rollback, restore from backup, rotate secrets), `docs/CONTENT_GUIDE.md` (how to add chapters, PDFs, videos and questions through the admin console).

## 3. How to work: segmental build with mandatory Opus review
Work in **segments**: one vertical slice each, small enough to review in one sitting. They follow the milestones in HANDOFF §11 (M0 → M6, plus Admin console and Ops hardening). For each segment:

1. **Brief:** write `docs/segments/<NN>-<name>.md` with scope, acceptance criteria (copy and refine from HANDOFF §8), the test list, and the screens and states to render.
2. **Choose the implementer by complexity:**
   - `implementer-sonnet` for bounded, pattern-following work: CRUD, simple components, migrations, wiring, tests.
   - `implementer-opus` for complex or high-risk work: LangGraph agent and SSE streaming, ink layer and Pencil latency, offline sync, auth/RLS, FSRS, the content renderer, the seed importer, exam-timer integrity, the admin editor, security.
   - When unsure, use Opus. Record the choice and reason in the brief.
3. **Implement with tests:**
   - Unit tests: vitest, pytest.
   - Integration tests against a real staging Supabase project; recorded fixtures are allowed only in unit tests.
   - Playwright e2e on WebKit, Chromium and Firefox at 820×1180, 1180×820, 390×844 (plus Android Pixel 412×915), 1440×900 and 1920×1080.
   - axe checks.
   - All green locally.
4. **Real renders:** run `ui-verifier` to capture and inspect screenshots of every changed screen in every state (populated, loading, empty, error, offline) at all four viewports, and compare them with the design boards.
5. **Flows:** script and run the end-to-end user flows for the segment against the deployed staging Space. Examples:
   - sign in with Google → onboarding → generated plan
   - open chapter → highlight → ask tutor (streams, cites) → save card → review card with FSRS
   - build a timed block → answer → results by system
   - upload a PDF → becomes a private chapter → searchable
   - pathway task → upload a vault document → cost tracked
   - admin uploads a figure and edits a chapter → the student sees it without a redeploy
6. **Mandatory review:** always run `reviewer-opus` on the segment, whoever implemented it. The reviewer runs everything itself and returns APPROVED or CHANGES REQUIRED. Fix every defect and re-run the review until APPROVED. A segment is done only when the Opus reviewer approves with zero failing or skipped tests, zero console errors and verified screenshots.
7. **Commit and deploy:** conventional commits, a PR per segment with the brief, test results and screenshot links, then merge to `main`, CI deploys to the Space, and smoke-test the live URL.
8. Update `docs/PROGRESS.md`: done, next, risks, decisions.

After every 3 segments, and before declaring any milestone complete, run `reviewer-opus` over the **whole app**: a full regression e2e suite, all flows, all screens. This catches cross-segment breakage.

## 4. Definition of done (whole project)
- Every screen in HANDOFF §8 is implemented against real data, with all acceptance criteria met on iPad, iPhone and laptop.
- All 14 seed chapters, 685 questions, 95 figures, the taxonomy, 16 pathway stages and 186 tasks are imported and rendered from the database and Storage. The admin console can add a new chapter, PDF, figure, YouTube video and question, and each appears live.
- The tutor works on every page: page-aware, streams, cites, uses free models with fallbacks, respects budget guards, is silent in exam mode, and asks for confirmation before writes.
- Offline week, ink with handwriting search, notes and highlights, the FSRS cards with Anki import/export, the planner with rebalance and ICS feed, and the pathway with vault and costs all work end to end.
- CI is green; the live Space passes smoke and e2e; backups restore successfully (tested once); docs are written.
- A final `reviewer-opus` full-app audit is APPROVED.

Begin now: read the files in §0, create the env files (§0.5) and pause for me, then write `docs/PLAN.md` and start segment 01 (M0 scaffold + tokens + CI + free Space deploy with `/api/health`).
