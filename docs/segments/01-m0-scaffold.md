# Segment 01 — M0: scaffold, tokens, contracts, health, CI, Docker Space deploy

**Implementer:** `implementer-opus`. Reason: this is the foundation every later segment inherits (contracts direction, deploy path, CI, security headers, logging), and mistakes here multiply.
**Reviewer:** `reviewer-opus` (mandatory).
**HANDOFF refs:** §2 (architecture), §3 (versions), §4 (design system), §9 (secrets), §10 (deploy), §11 M0. Rules: `CLAUDE.md`. Plan: `docs/PLAN.md`.

## Goal (M0 "done when")

The live Space URL shows the Naadi token sheet with a live system-health panel, and CI is green.

## Environment facts (read before starting)

- **Repo root:** `/home/user/naadi`. Seed data, config, design boards, schema, tools and deploy templates are at the root (`data/ config/ design/ db/ tools/ deploy/`). The spec is `handoff/HANDOFF.md`. Never build inside `handoff/`.
- **Secrets** are process environment variables (names in `docs/SECRETS.md`, template `.env.example`). `.env` is a local fallback only. Never print, log or commit values. `uv run scripts/check_env.py` verifies all of them (46 pass today).
- **The build sandbox has no DNS / raw TCP egress:** Postgres (`DATABASE_URL`, Supabase session pooler on 5432) is unreachable from here. HTTPS works (OpenRouter, Supabase REST/Storage/Auth, Tavily, Hugging Face, npm, PyPI). DB-backed tests run in GitHub Actions and against the deployed Space. Do not mock them to pass. In the sandbox you may use the Supabase Management API (`SUPABASE_ACCESS_TOKEN`, HTTPS) to verify DB state.
- **Official SDKs only:** `supabase` (py) / `@supabase/supabase-js`, `openrouter` (py), `tavily-python`, `huggingface_hub`, `psycopg` for Postgres. Raw `httpx` only where no SDK method exists, with a one-line comment why (e.g. Tavily usage endpoint, Supabase auth settings).
- **Toolchain here:** Node 22 + pnpm 10 are preinstalled; the spec pins **Node 24 LTS** and **pnpm 12.21.0**. Install Node 24 for this sandbox (official tarball into `~/.local`) and use corepack for pnpm. Python 3.13 + uv are present. `hf` CLI 2.4.0 is installed (`uv tool`).
- **Playwright:** a global 1.63 with Chromium only exists at `/opt/pw-browsers`. The project pins `@playwright/test@1.65.1`; install its Chromium, WebKit and Firefox for that version (`pnpm exec playwright install --with-deps chromium webkit firefox`). WebKit is mandatory (iPad target).
- **Hugging Face:** account `arrowassassin` is **PRO**. Space `arrowassassin/naadi` exists, private, currently Gradio SDK on `zero-a10g`, empty. It already has these secrets: `DATABASE_URL SUPABASE_SERVICE_ROLE_KEY OPENROUTER_API_KEY TAVILY_API_KEY ADMIN_EMAILS`, and variables: `SUPABASE_URL SUPABASE_ANON_KEY VITE_SUPABASE_URL VITE_SUPABASE_ANON_KEY OPENROUTER_DAILY_CAP OPENROUTER_APP_URL OPENROUTER_APP_TITLE GOOGLE_OAUTH_CONFIGURED APP_ENV APP_BASE_URL LOG_LEVEL GRADIO_SSR_MODE`. Owner approved switching it to **Docker SDK on `cpu-basic`** (keep it private).
- **GitHub:** this session can push to branch `claude/blissful-knuth-1gh22d` only. Do not open PRs, do not push to `main`. Actions secrets may not be set yet; CI jobs that need a secret must fail loudly with the missing name (never skip silently).

## Scope

### Monorepo

- Root: `package.json` (workspace scripts `dev`, `build`, `lint`, `format`, `typecheck`, `test`, `e2e`, `gen:contracts`), `pnpm-workspace.yaml`, `biome.json`, `tsconfig.base.json`, `.nvmrc`/`.node-version` (24), `engines` + `packageManager: pnpm@12.21.0`. Extend `.gitignore` (keep the `.env*` rules).
- Exact version pins from HANDOFF §3 (all verified to exist). Only add the packages M0 needs; later segments add theirs.
- `README.md` at the root: what Naadi is, layout, commands (as in `CLAUDE.md`).

### `packages/ui`

- `tokens.css`: every token from `design/System.dc.html` and HANDOFF §4 as CSS custom properties plus a Tailwind 4 `@theme` block: colours (ground, surface-1/2, line, text/-2/-3, paper, lime, lime-ink, violet, violet-ground, violet-text, amber, mark-amber, ink-violet, plus the extra greys used on the sheet, e.g. #141519 #23252B #34363D #3A3D45 #5A5D66 #8A8C92 #55575E #1F2126 #2A2D34 #2C2552), type scale (display/headline/lead/reading/label/ink), radii (12/22/32/999 and the HANDOFF ranges), spacing 4–64, touch targets (44/48/52–60), breakpoints (phone < 640, tablet 640–1100, laptop > 1100), motion (200 ms/400 ms ease-out, all off under `prefers-reduced-motion`), focus ring (3 px violet, 2 px offset).
- Fonts **self-hosted** via pinned `@fontsource` packages (Bricolage Grotesque, Inter, JetBrains Mono, Caveat): no third-party font requests (CSP, offline PWA).
- Components (React 19, typed props, accessible): `Button` (primary/strong/secondary/assistant/disabled, min 48 px), `Chip` (status chips always carry a word: Now/Done/Planned/Weak area/…), `Tile` (default/assistant/paper), `NavPill` (laptop/iPad), `TabBar` (phone, centre assistant button), `AssistantBar` (attach, input, send; 48 px targets), `Highlight` marks (key fact/formula/my mistake), `Skeleton`, `ErrorState`, `OfflineBanner`. Vitest + Testing Library unit tests.
- Colour roles are binding: lime = the one next action per view; violet = assistant; paper = student.

### `packages/shared` (contracts)

- **Direction: Pydantic is the source of truth.** `apps/api` exports OpenAPI 3.1 to `packages/shared/openapi.json` (committed). Generate a typed TS client **and zod schemas** from it (pick a maintained generator, e.g. `@hey-api/openapi-ts` with its zod plugin; pin the latest stable). The client validates every response with zod at runtime.
- `pnpm gen:contracts` regenerates everything; CI runs it and fails on `git diff --exit-code` (drift check).

### `apps/api` (Python 3.13, uv project)

- `pyproject.toml` with exact pins (HANDOFF §3 for what M0 uses: fastapi, uvicorn[standard], pydantic, pydantic-settings, psycopg[binary,pool], supabase, openrouter, httpx, a JSON logger such as structlog; dev: pytest, pytest-asyncio, ruff, mypy). `uv export` → `requirements.txt` for Docker (committed, CI checks it is current).
- `naadi_api/settings.py`: pydantic-settings; process env first, `.env` fallback; names from `docs/SECRETS.md` with aliases where useful; secrets as `SecretStr`; never logged or returned.
- `naadi_api/main.py`: app factory; `/api` router; serves the built SPA from `./static` with SPA fallback (unknown non-`/api` paths → `index.html`; unknown `/api/*` → JSON 404); long cache for hashed `/assets/*`, `no-cache` for `index.html`.
- **Logging:** structured JSON to stdout, one line per request with `request_id` (accept/propagate `X-Request-ID`, else generate), method, path, status, duration. Never log request/response bodies, auth headers, or query strings that may hold tokens.
- **Errors:** consistent JSON error shape `{error:{code,message,request_id}}`; no stack traces when `APP_ENV != local`; unhandled exceptions are logged with `request_id` (log-based error tracking).
- **Security headers** on every response: strict CSP (self for scripts/styles/fonts; `connect-src` self + the Supabase project origin; `img-src` self + Supabase Storage origin + data:; **`frame-ancestors 'self' https://huggingface.co`** because HF renders Spaces inside an iframe on huggingface.co), HSTS, `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`. No inline scripts in the SPA (the CSP must not need `unsafe-inline` for scripts).
- `GET /api/health`: components `database` (psycopg async pool `select 1`), `storage` (supabase SDK `storage.list_buckets()` with the service role), `openrouter` (SDK `api_keys.get_current_key_metadata()`: report `free_model_daily_requests`/limit info and `is_free_tier`; never the key), `tavily` (credit guard: credits used this month vs `monthly_credit_guard` from `config/models.yaml`; Tavily's SDK has no usage call, so use its usage endpoint over HTTPS with a comment; **never spends a credit**). Each component: `status` ok/degraded/down, `latency_ms`, short `detail` with no secrets. Cache external checks (OpenRouter 60 s, Tavily 5 min) so the public endpoint can't be used to burn quotas. Overall status; HTTP 200 when ok/degraded, 503 when the database is down. Pydantic response model (so it is in OpenAPI).
- `GET /api/ready`: settings loaded and DB pool open → 200, else 503.
- `naadi_api/export_openapi.py` used by `gen:contracts`.
- Tests: pytest units for settings precedence, logging (request_id present, no bodies), error shape, headers/CSP, SPA fallback, health aggregation with faked components (fakes/recorded fixtures are allowed in unit tests only). Integration tests (marker `integration`): health against the real services. DB-dependent ones carry marker `db` and run in CI; in the sandbox run `-m "integration and not db"`. Do not use `skip` decorators to hide failures.

### `apps/web` (Vite 8 + React 19 + TanStack Router/Query + Tailwind 4)

- Route `/` (and `/system`, which stays as the design-system reference after `/` becomes Today in later segments): the **token sheet** rebuilt from `design/System.dc.html` in our own components (colour swatches from the CSS variables, type scale, radius/spacing/targets, component specimens), plus a **live health panel** that calls `/api/health` through the generated client: loading (skeletons), populated, error (API 500/503, visible message + retry), offline (`navigator.onLine` false → offline banner). Status chips carry words, not only colours.
- Fluid layout across phone/tablet/laptop (no fixed board width; max content width 1320; no horizontal scroll at 390 px).
- `vite.config` proxies `/api` → `http://localhost:7860` in dev. `index.html` without inline scripts.
- The web bundle ships **zero content and zero business config** (the token sheet is design-system UI, not content; health values come from the API).
- Vitest unit tests for the health panel states.

### E2E (Playwright 1.65.1 + @axe-core/playwright)

- Projects: WebKit, Chromium, Firefox × viewports 820×1180, 1180×820, 390×844, 412×915, 1440×900, 1920×1080 (use touch/mobile emulation where the browser supports it).
- Tests: token sheet renders; health panel shows each state (populated against the running API; error and offline via route interception / `context.setOffline`); no horizontal overflow; keyboard focus ring visible; **zero axe violations (WCAG 2.2 AA tags)**; **zero console errors**.
- `pnpm e2e` runs against `uvicorn` serving the production build (`./static`) on :7860; `E2E_BASE_URL` overrides for the live Space, adding `Authorization: Bearer $HF_TOKEN` when the Space is private.
- Screenshots of every state at every viewport saved under `test-results/screens/01/`.

### Docker Space deploy (`deploy/space/`, owner approved)

- Adapt `deploy/space/Dockerfile` (multi-stage: `node:24-slim` + pnpm 12.21.0 builds the web; `python:3.13-slim` runs uvicorn on 7860 as uid 1000) and its `README.md` card (`sdk: docker`, `app_port: 7860`). Public build args `VITE_SUPABASE_URL`/`VITE_SUPABASE_ANON_KEY` come from Space variables.
- `scripts/deploy_space.sh`: assembles a clean build context (root workspace files, lockfile, `packages/`, `apps/web`, `apps/api`, `config/`, Dockerfile, Space README) and uploads with the `hf` CLI / `huggingface_hub`. First run also switches the Space to `sdk: docker` and requests `cpu-basic` hardware (`HfApi.request_space_hardware`). Waits for `RUNNING`, then smoke-tests `/api/health` and `/` with the HF token header. Never prints secrets.
- Verify on the live Space: `hf spaces info … --expand runtime`, logs, `curl /api/health` (DB reachable from the Space proves `DATABASE_URL`), Playwright WebKit smoke against the live URL.

### CI (`.github/workflows/`)

- `ci.yml` on push and PR: install (pnpm + uv, cached) → lint (biome, ruff) → typecheck (tsc, mypy) → unit (vitest, pytest) → contracts drift → requirements drift → `tools/check_models.py` → build → integration (incl. `db`) → e2e matrix (with browsers installed) → on `main` only: deploy to the Space + live smoke.
- `cron.yml`: daily 03:00 UTC `GET /api/health` on the live Space (Supabase keep-alive, Space warm-up). Backups come in segment 22.
- Port `tools/check_models.py` to the official `openrouter` SDK (`models.list()`) and enforce `only_free_ids_in_chains` (every primary/fallback ID ends with `:free`, except the explicit free router `openrouter/free`). Exit 1 if a primary is missing or lacks a required capability.
- Jobs needing secrets read them from Actions secrets/variables named exactly as in `docs/SECRETS.md`; if one is missing, fail with a clear message naming it.

## Acceptance criteria

1. `pnpm lint`, `pnpm typecheck`, `pnpm test`, `uv run pytest -m "not db"` are green in the sandbox; the `db` tests are written and wired into CI.
2. `pnpm gen:contracts` produces no diff; the web uses only the generated client; responses are zod-validated.
3. `pnpm e2e` green on WebKit, Chromium and Firefox at all six viewports with zero axe violations and zero console errors; screenshots saved for all states.
4. `/api/health` returns all four components with correct status and no secret material; `/api/ready` works; JSON logs carry `request_id`; security headers and CSP present (tested).
5. Space switched to Docker on `cpu-basic`; live URL (with HF auth) serves the token sheet; live `/api/health` shows `database: ok` (proves the pooler URL and password); live WebKit smoke passes.
6. CI workflows committed and syntactically valid; jobs fail loudly when a required secret is missing.
7. Nothing hardcoded in `apps/web` beyond design-system UI; no TODO/FIXME/placeholder/lorem/mock in shipped code.

## Screens × states to render

`/` (token sheet + health panel): populated, loading, error, offline, at 820×1180, 1180×820, 390×844, 412×915, 1440×900, 1920×1080. Compare with `design/System.dc.html`.

## Out of scope (later segments)

DB schema/migrations (02), auth (03), seed (04), any content screens.

## Report back

Files changed, commands run with results (counts of tests passed/failed/skipped per suite), live URL checks, screenshots path, anything not finished, risks, and spec ambiguities you resolved.

## Updates since this brief was written (binding; they override anything above)

1. **CI secrets:** GitHub Actions has exactly ONE secret, `HF_TOKEN` (already added). The repo is **public**: ubuntu runners only, concurrency groups to cancel superseded runs. Public config CI needs must not be Actions secrets; the Space already holds everything at runtime.
2. **DB tests in CI** run against a `services:` container of Supabase's real Postgres image `supabase/postgres`, pinned to a plain 17.11.x tag (not `-orioledb` / `-multigres`). It ships the anon/authenticated/service_role roles and the `auth` schema. No Supabase secrets in CI.
3. **Deploy job on `main` only:** `HF_TOKEN` → upload to `arrowassassin/naadi`, wait for RUNNING, smoke `/api/health` (database ok) and a Playwright WebKit smoke against the live URL with the HF token header. Missing `HF_TOKEN` fails loudly. From this sandbox you may also deploy directly with `scripts/deploy_space.sh` (HF_TOKEN is in the environment).
4. **`docs/SECRETS.md`:** GitHub Actions column is "—" for everything except `HF_TOKEN`; instructions say to add `HF_TOKEN` once in GitHub → Settings → Secrets and variables → Actions. No `gh secret set` loop.
5. **Aegra later:** keep `naadi_api.main:app` a self-contained FastAPI app (own lifespan, middleware, `/api` routes, SPA fallback) so Aegra can host it via `aegra.json` `http.app` in segment 04. Do not add Aegra or langgraph now.
6. **`config/models.yaml` changed shape:** top-level `providers:` (`nvidia`, `openrouter`, each with `key_env`, `rpm`) and per-role ordered `chain:` of `{provider, model}`; a `generation:` block that is not an LLM (ignore it). `tools/check_models.py`: OpenRouter entries validated against the public catalog via the `openrouter` SDK with `:free` / `openrouter/free` enforced; NVIDIA entries must be **probed** with a tiny call via `langchain-nvidia-ai-endpoints` `ChatNVIDIA` because the catalog lists retired models (see `docs/MODEL_TESTS.md`). CI has no NVIDIA key, so it prints an explicit "not probed in CI; probed by the Space health check" line for those (not a failure, not silent). Required capabilities: tutor → tools; vision → image input; structured → structured output. No model IDs hardcoded in code or tests.
7. **`/api/health`** reports both LLM providers: OpenRouter key metadata (SDK) and an NVIDIA probe of the configured NVIDIA models (cached ≥ 1 h so it cannot burn quota). NVIDIA access from the Space uses the `NVIDIA_API_KEY` Space secret; if it is not set on the Space yet, report `nvidia: not_configured` (degraded, not down).
8. **Sandbox NVIDIA access:** this session has no `NVIDIA_API_KEY` variable; requests to `integrate.api.nvidia.com` get the key injected by the network proxy. For local runs set `NVIDIA_API_KEY=nvapi-proxy-injected` as a placeholder.
9. **Supabase Management API** (`SUPABASE_ACCESS_TOKEN`, HTTPS) may be used from the sandbox to run SQL against the real project when you need to verify DB state.
10. **Resuming:** a previous implementer built most of this segment and stopped. Its work is committed (`12b3189` and earlier). Start by reading the tree and `git log`, run the existing tests, and finish what's missing. Don't rebuild what works.
