# Secrets and configuration

Every runtime value Naadi reads, where it comes from, and what it is called in each place. Values are never committed, logged or printed.

**Load order** (`apps/api/naadi_api/settings.py`, `scripts/check_env.py`): process environment first, then `.env` in the repo root as a local-dev fallback (gitignored). The template with comments is [`.env.example`](../.env.example).

**Where values live:**
- **Claude Code cloud environment:** environment variables in the environment settings (used by this build session).
- **Hugging Face Space** `arrowassassin/naadi`: Space *secrets* (private) and *variables* (visible to anyone who can see the Space). `scripts/push_space_secrets.sh` syncs them.
- **GitHub Actions:** repository *secrets* (CI integration tests, deploy, backup cron) and *variables*.

Kind: **secret** = private everywhere. **public** = may reach the browser. **config** = non-secret server setting.

| Key | Kind | Source | HF Space | GitHub Actions | Needed by |
|---|---|---|---|---|---|
| `DATABASE_URL` | secret | Supabase → Project Settings → Database → Connection string → **Session pooler** (port 5432) + `?sslmode=require` | secret `DATABASE_URL` | secret `DATABASE_URL` | API, migrations, seed, integration tests, nightly `pg_dump` |
| `SUPABASE_URL` | public | Supabase → Project Settings → API → Project URL | variable `SUPABASE_URL` | variable `SUPABASE_URL` | API (auth, storage) |
| `SUPABASE_ANON_KEY` | public | Supabase → Project Settings → API Keys → anon / publishable | variable `SUPABASE_ANON_KEY` | variable `SUPABASE_ANON_KEY` | API, e2e sign-in |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | Supabase → Project Settings → API Keys → service_role / secret | secret `SUPABASE_SERVICE_ROLE_KEY` | secret `SUPABASE_SERVICE_ROLE_KEY` | API (shared-content writes, storage signing), seed, test-user setup |
| `SUPABASE_JWT_SECRET` | secret | Supabase → Project Settings → JWT Keys → Legacy JWT secret. Optional if the project only uses asymmetric keys (JWKS) | secret `SUPABASE_JWT_SECRET` | secret `SUPABASE_JWT_SECRET` | API JWT verification |
| `VITE_SUPABASE_URL` | public | = `SUPABASE_URL` | variable `VITE_SUPABASE_URL` | variable `VITE_SUPABASE_URL` | Web build (Vite) |
| `VITE_SUPABASE_ANON_KEY` | public | = `SUPABASE_ANON_KEY` | variable `VITE_SUPABASE_ANON_KEY` | variable `VITE_SUPABASE_ANON_KEY` | Web build (Vite) |
| `OPENROUTER_API_KEY` | secret | openrouter.ai → Settings → Keys | secret `OPENROUTER_API_KEY` | secret `OPENROUTER_API_KEY` | `naadi_api/llm.py`, `tools/check_models.py`, health check |
| `OPENROUTER_DAILY_CAP` | config | `50`, or `1000` after the one-time $10 credit purchase | variable `OPENROUTER_DAILY_CAP` | variable `OPENROUTER_DAILY_CAP` | LLM budget guard |
| `OPENROUTER_APP_URL` | config | Live app URL, e.g. `https://arrowassassin-naadi.hf.space` | variable `OPENROUTER_APP_URL` | — | `HTTP-Referer` header |
| `OPENROUTER_APP_TITLE` | config | `Naadi` | variable `OPENROUTER_APP_TITLE` | — | `X-Title` header |
| `TAVILY_API_KEY` | secret | app.tavily.com → API Keys | secret `TAVILY_API_KEY` | secret `TAVILY_API_KEY` | `web_search` tool, health check |
| `HF_TOKEN` | secret | huggingface.co → Settings → Access Tokens (**write**) | — (not given to the Space: least privilege) | secret `HF_TOKEN` | CI deploy, nightly backup upload |
| `HF_SPACE_ID` | config | `arrowassassin/naadi` | — | variable `HF_SPACE_ID` | CI deploy, smoke tests |
| `HF_BACKUP_DATASET` | config | Private dataset, e.g. `arrowassassin/naadi-backups` | — | variable `HF_BACKUP_DATASET` | Nightly `pg_dump` |
| `ADMIN_EMAILS` | secret (PII) | Comma-separated admin emails | secret `ADMIN_EMAILS` | secret `ADMIN_EMAILS` | Admin role gate (server side) |
| `GOOGLE_OAUTH_CONFIGURED` | config | `true` once Google is enabled in Supabase → Auth → Providers (client ID/secret live only in Supabase) | variable `GOOGLE_OAUTH_CONFIGURED` | variable `GOOGLE_OAUTH_CONFIGURED` | Sign-in screen shows the Google button |
| `YOUTUBE_API_KEY` | secret, optional | Google Cloud → YouTube Data API v3 key | secret `YOUTUBE_API_KEY` | secret `YOUTUBE_API_KEY` | `youtube_mapper.py` (weekly cron, M6) |
| `R2_ACCOUNT_ID` | config, optional | Cloudflare dashboard → R2 | variable `R2_ACCOUNT_ID` | — | Large uploads (M5) |
| `R2_ACCESS_KEY_ID` | secret, optional | Cloudflare → R2 → Manage API tokens | secret `R2_ACCESS_KEY_ID` | — | Presigned URLs |
| `R2_SECRET_ACCESS_KEY` | secret, optional | Cloudflare → R2 → Manage API tokens | secret `R2_SECRET_ACCESS_KEY` | — | Presigned URLs |
| `R2_BUCKET` | config, optional | R2 bucket name | variable `R2_BUCKET` | — | Large uploads |
| `R2_PUBLIC_BASE_URL` | config, optional | r2.dev or custom domain | variable `R2_PUBLIC_BASE_URL` | — | Own-video URLs |
| `APP_ENV` | config | `local` \| `staging` \| `prod` | variable `APP_ENV` (`prod`) | variable `APP_ENV` (`staging` for CI) | Logging, error detail |
| `APP_BASE_URL` | config | `http://localhost:5173` locally, the Space URL in prod | variable `APP_BASE_URL` | variable `APP_BASE_URL` | Auth redirects, ICS links |
| `LOG_LEVEL` | config | `INFO` | variable `LOG_LEVEL` | — | Structured logging |
| `GRADIO_SSR_MODE` | config (fixed) | Always `false` | variable `GRADIO_SSR_MODE` | — | Free Gradio/ZeroGPU Space (HANDOFF §2) |

## Commands

Verify everything (prints pass/fail only, never values; the Tavily check costs 1 credit):

```bash
uv run scripts/check_env.py
```

Push to the Space (asks for confirmation; `--dry-run` lists names only):

```bash
scripts/push_space_secrets.sh
```

Set GitHub Actions secrets from the environment (the value is read from stdin, so it never appears in argv):

```bash
for k in DATABASE_URL SUPABASE_SERVICE_ROLE_KEY SUPABASE_JWT_SECRET OPENROUTER_API_KEY TAVILY_API_KEY HF_TOKEN ADMIN_EMAILS YOUTUBE_API_KEY; do [ -n "${!k}" ] && printf '%s' "${!k}" | gh secret set "$k" --repo arrowassassin/naadi; done
```

## Rotation

See `docs/RUNBOOK.md` → "Rotate secrets" (written in the Ops segment). In short: create the new value at the source, update the cloud environment, re-run `scripts/push_space_secrets.sh` and the `gh secret set` loop, restart the Space, run `check_env.py`, then revoke the old value.
