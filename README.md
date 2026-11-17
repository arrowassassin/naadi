# Naadi

Naadi is a USMLE study app for international medical graduates, built iPad-first: learn a topic, practise
questions, plan the weeks to exam day, with an assistant that sees the page you are on. It runs entirely on
free tiers (Hugging Face Space on `cpu-basic`, Supabase Free, free LLM endpoints, Tavily Free).

The product and engineering spec is [`handoff/HANDOFF.md`](handoff/HANDOFF.md); the working agreement is
[`CLAUDE.md`](CLAUDE.md); the build plan and decisions are in [`docs/PLAN.md`](docs/PLAN.md).

## Layout

```
apps/web         Vite 8 + React 19 SPA/PWA (TanStack Router/Query, Tailwind 4)
apps/api         Python 3.13 FastAPI app (naadi_api/): serves the built SPA at / and the API at /api/*
packages/ui      tokens.css (from design/System.dc.html) and shared React components
packages/shared  OpenAPI from the API -> generated TS client + zod schemas (Pydantic is the source of truth)
config/          models.yaml (LLM provider chains, budgets), sources, authoring rules
data/            seed content
design/          Claude Design boards: visual reference only, never imported
deploy/space/    Dockerfile + Space card for the single Docker Space
scripts/ tools/  env check, Space deploy, model-chain check, content tools
docs/            plan, progress, secrets map, segment briefs
```

## Commands

Requires Node 24 LTS with corepack (pnpm 12.21.0 comes from `packageManager`), Python 3.13 and uv.

| Command                                                                                 | What it does                                                                                                                             |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm install` and `uv --directory apps/api sync`                                       | install everything                                                                                                                       |
| `pnpm dev`                                                                              | web on :5173, proxying `/api` to :7860                                                                                                   |
| `pnpm dev:api` (`uv run uvicorn naadi_api.main:app --reload --port 7860` in `apps/api`) | API                                                                                                                                      |
| `pnpm lint` · `pnpm typecheck` · `pnpm format`                                          | biome + ruff · tsc + mypy · auto-format                                                                                                  |
| `pnpm test`                                                                             | vitest (ui, shared, web)                                                                                                                 |
| `uv run pytest -m "not db"` (in `apps/api`)                                             | API unit + external-service integration tests; `-m db` needs Postgres                                                                    |
| `pnpm e2e`                                                                              | Playwright (WebKit, Chromium, Firefox x 6 viewports) against uvicorn serving the production build; `E2E_BASE_URL` targets the live Space |
| `pnpm gen:contracts`                                                                    | export OpenAPI and regenerate the TS client + zod schemas (CI fails on drift)                                                            |
| `pnpm gen:requirements`                                                                 | `uv.lock` -> `apps/api/requirements.txt` for Docker (CI fails on drift)                                                                  |
| `uv run scripts/check_env.py`                                                           | verify every env var and live service (never prints values)                                                                              |
| `uv run python ../../tools/check_models.py` (in `apps/api`)                             | verify the LLM provider chains in `config/models.yaml`                                                                                   |
| `scripts/deploy_space.sh`                                                               | build context -> Space upload -> wait for RUNNING -> live smoke (`HF_TOKEN` required)                                                    |

Secrets are environment variables, named in [`docs/SECRETS.md`](docs/SECRETS.md) (template: `.env.example`).

## Health

`GET /api/health` reports the database, Supabase Storage, the OpenRouter free quota, a probe of each NVIDIA
model in the role chains and Tavily credits; `GET /api/ready` reports readiness. The token sheet at `/` (and
`/system`) shows the design system with a live health panel, including Backups (encrypted, private dataset) and,
on the Space, SearXNG.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): processes, data flow, agent graphs and crons, budgets, code map
- [`docs/SECURITY.md`](docs/SECURITY.md): threat model, privacy, headers and CSP, rate limits, ASVS 4.0.3 Level 2
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md): deploy and rollback, backups and restore, rotating secrets, free-tier incidents, SearXNG, seed
- [`docs/CONTENT_GUIDE.md`](docs/CONTENT_GUIDE.md): sources and licences, authoring pipeline, YouTube rules, coverage goals
- [`docs/SECRETS.md`](docs/SECRETS.md): every environment variable, where it comes from and who sets it

## License

See [`LICENSE`](LICENSE).
