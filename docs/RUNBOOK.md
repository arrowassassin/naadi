# Naadi runbook

Operational procedures. Secrets are never typed into chat, logs or commands shown here; every command reads
them from the environment (docs/SECRETS.md).

## Seed content (S05)

The importer (`apps/api/naadi_api/seed/`) loads `data/` + `config/` into Postgres and the `content-public`
bucket. It is idempotent: a second run reports `changes: 0`. Groups run in this order, each in its own
transaction: `sources, taxonomy, figures, chapters, questions, pathway, plan, app_config`.

### When the seed data changes

1. Edit `data/` (or regenerate it with `tools/`).
2. If `data/chapters.json` changed: `pnpm --filter @naadi/shared seed:convert` and commit
   `data/generated/section_bodies.json` (CI fails when it is stale; the importer refuses stale sections).
3. Dry run locally, then deploy, then seed the real project (below).

### Local / CI

```bash
NAADI_PG_CONTAINER=pg-naadi NAADI_STORAGE_PORT=54335 apps/api/tests/db/stack.sh up
cd apps/api
export DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres NAADI_STORAGE_URL=http://localhost:54335
export SUPABASE_SERVICE_ROLE_KEY="$(uv run python -c 'from tests.db.storage import local_service_key; print(local_service_key())')"
uv run python -m naadi_api.migrate
uv run python scripts/seed.py --data ../../data --config ../../config --dry-run   # prints the plan, writes nothing
uv run python scripts/seed.py --data ../../data --config ../../config --report /tmp/seed.json
```

A local database with a remote Storage (or the reverse) is refused (exit 2) unless `--allow-split-target`,
so a local run can never upload into the real project's bucket.
Sources without complete license fields are skipped and listed under `license gate` in the report; they do
not fail the run. Every other check, the counts included, must pass.

Exit codes: 0 ok, 1 verification failed (report printed), 2 invalid bundle or configuration, 3 another
import holds the lock.

### Real project (from the Space, so no database URL or service key leaves it)

The Space has `DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as secrets and ships `data/`.
Signed in as an admin (an `ADMIN_EMAILS` address), with the access token from the web app session:

1. Dry run: `POST /api/admin/seed` with `{"dry_run": true}` (202, returns the run). Poll
   `GET /api/admin/seed/runs/{id}` until `status` is not `running`; read `report` (inserted/updated per table,
   checks).
2. Real run: `POST /api/admin/seed` with `{"dry_run": false}`. Same polling; `status: succeeded` and
   `report.ok: true` are required.
3. Re-run step 2 once: `report` must show 0 inserted/updated/deleted and `storage.uploaded: 0`.
4. Verify read-only from outside: `cd apps/api && uv run python -m tests.db.live_check --seed`
   (needs `SUPABASE_URL` and `SUPABASE_ACCESS_TOKEN`; Management API, `read_only: true`).
5. `GET /api/health` stays `ok`.

Options: `"only": ["app_config"]` runs a subset; `"force": true` also overwrites sections and `app_config`
keys an admin edited (otherwise they are kept and counted as `kept`). Only one job runs at a time (409
otherwise). A job left `running` by a restarted container is marked failed by the next start after 15 min.

One-off container alternative (same code path): `python -m naadi_api.seed --dry-run` (then without
`--dry-run`) in the Space's container, which reads the same secrets.

### Edits and the seed

- `app_config`: an admin PUT sets `admin_edited_at` (and `updated_by`); the seed never overwrites such a key
  without `force`, even if the admin's account is later deleted. A forced run clears the mark.
  Every change is in `app_config_history` (GET `/api/admin/config/{key}/history`).
- `ui_copy` and newly required keys: if a stored (admin-edited) `ui_copy` lacks keys a newer release requires,
  the API serves stored ∪ shipped (`config/app_config.yaml`), the stored strings winning, and reports it as
  "N missing keys filled" on `GET /api/admin/config/ui_copy`. Saving it once from the admin editor
  stores the merged value. Only a row that is invalid for another reason falls back to the shipped value whole.
- Chapter sections: a non-seed revision (admin editor, assistant) sets `admin_edited_at` by trigger, which
  protects the section. When the seed itself
  replaces a body, the previous body is stored as a `seed` revision in `chapter_section_revisions`.

## Sign-in check on the live Space (S03): `pnpm e2e:live-auth`

Run from the sandbox after every deploy (the CI live smoke is public-only). Needs `E2E_BASE_URL` (the Space
URL), `HF_TOKEN` + `HF_SPACE_ID` (private Space: the token is exchanged for the Space's own cookie),
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `ADMIN_EMAILS`. The task sets `E2E_ALLOW_HOSTED=1` itself;
every other e2e entry point refuses a non-local `SUPABASE_URL` without it.

What it does, in WebKit at iPad portrait:

1. Deletes leftover `naadi-e2e-live-*@example.com` users from a crashed earlier run.
2. Creates a throwaway `naadi-e2e-live-<run>@example.com` user, signs it in through the Space's own
   `/auth/callback` with a server-generated magic-link token, checks `/api/me` (profile, `is_admin` false)
   and a 403 from `/api/admin/overview`, and deletes the user at the end whatever happens.
3. Checks that the owner (the first `ADMIN_EMAILS` address) is an admin, through the API only: the address
   is never printed and no page renders that account. The session created for the check is revoked at
   once (the test asserts the logout returns 204).

Side effects on the owner's account to know about: `--owner` generates a magic-link token for the owner,
which **replaces any magic link the owner has outstanding** (an unopened sign-in email stops working), and
the verification **updates the owner's "last sign-in" time** in Supabase Auth. Nothing else changes.


## Local search (S07)

- **Index:** `public.embeddings` is built from the content by fastembed (models named in `config/models.yaml` `roles.embeddings`, cached in `FASTEMBED_CACHE_PATH`; the image prefetches them). It is reconciled at API start (`EMBEDDINGS_AUTOSTART`, default on), after every successful non-dry-run seed job that touches chapters, questions or pathway, by an admin (`POST /api/admin/embeddings/reindex`, status at `GET /api/admin/embeddings`: row counts per type, `models_ready`, last job), or from a shell: `python -m naadi_api.embeddings reindex [--kinds ...] [--refs ID,ID] [--threads N]`. Unchanged chunks are skipped, so a repeat run costs a read. A first full run embeds about 1,600 chunks; it runs on a low-priority thread so searches stay responsive.
- **What is never indexed:** mock exam sections and questions (their answer keys are not content; the seed importer also strips the key tables), anything whose source lacks `allow_ai_ingest`, drafts and retired rows, question answers and explanations.
- **Post-deploy smoke (lead runs it at deploy):** with a signed-in user's access token in `SMOKE_ACCESS_TOKEN` (never on the command line), `cd apps/api && uv run python scripts/smoke_search.py --base-url https://<ns>-naadi.hf.space`. It queries with the live chapter titles and exits 1 unless wall-clock p95 of `GET /api/search` is under 300 ms and every request answered. CI's e2e job runs the same measurement on a shared runner with a looser gate (p95 < 1000 ms, numbers in the job log), because shared runners are not the Space.

## Deploy and rollback (S22)

- **Deploy:** merge to `main`. `ci.yml` runs; when it is green, `deploy.yml` (the only workflow with `HF_TOKEN`)
  assembles the build context from tracked files (`scripts/deploy_space.py`: no `.env*`, every secret value scanned
  for), uploads it to the Space, waits for RUNNING and smoke-tests `/api/health` (database ok) and `/`. A newer push
  never cancels a deploy in progress.
- **Watch:** Space → Logs. A failed migration prints one `migrate_all.failed` line and the container exits, so the
  old revision keeps serving until the new one is healthy.
- **Rollback (code):** revert the bad commit on `main` (`git revert <sha>`) and push; CI and the deploy run as usual
  and the Space comes back on the previous code. History on `main` is never rewritten. If CI itself is the problem,
  Actions → deploy → Run workflow (on `main`) redeploys the current `main` without waiting for CI.
- **Rollback (schema):** migrations are forward-only in production. Each one has a real `downgrade()` for local use,
  but a bad migration on the live database is fixed by a new migration. If data was damaged, restore (below) into a
  scratch project, compare, and copy back the affected rows.

## Backups and restore (S22)

**What runs:** on the Space, as Aegra system crons (no GitHub secret, no Actions minutes):

| Job | When (UTC) | Writes |
|---|---|---|
| `naadi.backup.nightly` | 03:00 daily | `db/YYYY-MM-DD.dump.age` (`pg_dump --format=custom --no-owner`, schema `public`, `search_cache` rows excluded) and `db/YYYY-MM-DD.auth.dump.age` (data of `auth.users`, `auth.identities`) |
| `naadi.backup.storage_weekly` | Sunday 03:30 | `storage/YYYY-MM-DD.tar.gz.age`: every object of `user-uploads`, `vault`, `generated-media`, and `content-public`/`content-private` except the seed's `figures/` |
| `naadi.prune.daily` | 04:11 daily | deletes rows past retention (ARCHITECTURE "Budgets") |

Files are encrypted with age to `BACKUP_AGE_RECIPIENT` and committed to the private dataset `HF_BACKUP_DATASET`
(default `arrowassassin/naadi-backups`) with `HF_BACKUP_TOKEN`. The same commit deletes database files older than 30
days and Storage tarballs older than 35. Each run records one `agent_health` row (kind `backup`); the health panel
(`/system`) shows **Backups**: *Not configured* until both settings exist, then the last successful times, *Degraded*
when the last run failed or the last database backup is older than 30 hours.

**One-time setup (owner):**
1. On your own machine: `age-keygen -o naadi-backup.agekey`. Keep the file offline (password manager + one offline
   copy). The `# public key: age1…` line is the recipient.
2. Create the dataset yourself (huggingface.co → New dataset → **Private**), named as `HF_BACKUP_DATASET`. The app never
   creates it and refuses to upload to a dataset that is not private.
3. Make a fine-grained token with write access to that one dataset only.
4. Space → Settings: secret `HF_BACKUP_TOKEN`, variable `BACKUP_AGE_RECIPIENT`. Restart the Space.
5. Next morning the panel shows the first backup; or run it now from `/admin/agent` (graph `backup`, input
   `{"mode": "db"}`) as an admin.

**Restore** (into an EMPTY Supabase project; the free plan allows a second one; never over the live database):
1. Create the scratch project (Supabase dashboard → New project). Its Auth and Storage schemas exist from the start.
2. On your machine, in `apps/api`: `uv sync`, PostgreSQL 17 client tools (`pg_restore`, `psql`) on PATH or `--pg-bin`.
3. Check out the release that made the backup (the archive's Alembic revision must equal the code's head; the script
   says so if not).
4. Run (the target URL comes from the environment, never the command line; use the scratch project's direct
   connection string):
   ```bash
   export TARGET_DATABASE_URL='postgresql://postgres:…@db.<scratch-ref>.supabase.co:5432/postgres'
   export HF_BACKUP_TOKEN=…            # read access to the dataset is enough
   uv run python scripts/restore.py --date 2026-11-17 --identity ~/naadi-backup.agekey \
     --repo arrowassassin/naadi-backups --seed-checks
   ```
   It decrypts, runs `migrate_all` against the target (schema, RLS, buckets, policies as on the Space), empties
   `public`, loads the auth users and then `public` in one transaction with triggers off, and verifies: Alembic head,
   every live_check schema/RLS/Storage-policy check, and (with `--seed-checks`) the seed counts.
5. Storage: add `--storage` with `TARGET_SUPABASE_URL` and `TARGET_SERVICE_ROLE_KEY` (the scratch project's service
   key) to re-upload objects from the nearest weekly tarball; re-run the seed (`scripts/seed.py`) for the figures.
6. To switch production to the restored project: set `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `SUPABASE_SERVICE_ROLE_KEY` (and the Auth settings: site URL, Google OAuth) on the Space, then restart.

**Tested:** every push that touches the backup code, and weekly, by `.github/workflows/restore.yml`: a seeded source
database is backed up with the real code (throwaway age key, local test path) and restored into a second fresh
`supabase/postgres`, with all checks above plus an exact row-count match on every table. The manual drill into a scratch
Supabase project needs the owner's account and keys (open item for the owner; record the date and result here when done).

## Rotating secrets

Rotate one at a time, then watch `/api/health`:

| Secret | Where to rotate | Then |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` | Supabase → Settings → API (JWT keys: rotate the signing key; JWKS publishes the new one) | update the Space secrets, restart; sessions continue (asymmetric keys) |
| `DATABASE_URL` password | Supabase → Database → Reset password | update the Space secret, restart |
| `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `TAVILY_API_KEY`, `YOUTUBE_API_KEY` | the provider's dashboard | update the Space secret; delete the old key at the provider |
| `HF_TOKEN` (deploy) | huggingface.co → Settings → Access Tokens | GitHub → Settings → Secrets → Actions `HF_TOKEN`; revoke the old |
| `HF_BACKUP_TOKEN`, `HF_GEN_TOKEN` | huggingface.co, fine-grained, same scope as before | Space secret, restart |
| `BACKUP_AGE_RECIPIENT` | `age-keygen` a new pair | Space variable; KEEP the old private key: older backups need it |
| VAPID keys (Web Push) | generate a new pair | Space secrets; devices re-subscribe on next visit |

`scripts/push_space_secrets.sh` sets them from your local environment without printing values;
`uv run scripts/check_env.py` verifies every variable and live service (names and statuses only).

## Free-tier incidents

- **LLM quota exhausted** (OpenRouter 50/day, or the daily cap): the assistant and jobs show their "budget spent"
  state; the coach falls back to rules wording; nothing errors. Health → OpenRouter shows the quota. It resets at
  00:00 UTC. A one-time $10 credit raises the free cap to 1,000/day (then set `OPENROUTER_DAILY_CAP`).
- **Tavily credits:** web search stops at 950 of 1,000 monthly credits; layers 1–5 and SearXNG (if enabled) keep working.
- **Supabase paused** (7 days without requests): the daily `cron.yml` ping prevents it. If it happens: Supabase
  dashboard → Restore project, then check `/api/health`.
- **Space asleep** (48 h idle on cpu-basic): the first request wakes it (cold start ~1–2 min, embeddings models are in
  the image). The daily ping keeps it warm; Aegra's crons only fire while it is awake.
- **Rate limited (429):** responses carry `Retry-After`. Per-user buckets are in SECURITY.md; a flood from one address
  hits the 600/min per-client floor first.

## SearXNG (search layer 8)

SearXNG runs in the Space container on `127.0.0.1:8888` (pinned commit, `deploy/searxng/settings.yml`: JSON format on,
engines limited to DuckDuckGo, Brave, Mojeek and Wikipedia, no image engines). Health → **SearXNG** shows whether it
answers. Students' searches use it only when `app_config.search.layers.searxng.enabled` is `true`.

To enable (admin, no redeploy):
1. On the Space, check Health → SearXNG is *OK* and memory stays within `cpu-basic` (Space → Metrics; SearXNG idles at
   about 100–150 MB).
2. As an admin, `GET /api/admin/search/searxng-probe` (signed-in admin token): it sends one fixed medical query to
   the Space's SearXNG through the real client and parser (`naadi_api/search.py` `parse_searxng`) and returns the
   result count, the engines that answered and the latency, without enabling the layer for students. Expect at least
   one https result from two engines. The parser and fallback rules are covered offline by
   `tests/agent/test_search_unit.py` (CI).
3. `/admin/config` → `search` → `layers.searxng.enabled: true` → Save. Revert the same way if engines throttle the
   Space's IP (results show `searxng` errors in the search log).

To stop it entirely, set the Space variable `NAADI_SEARXNG=off` and restart.
