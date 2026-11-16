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

- `app_config`: an admin PUT sets `updated_by`; the seed never overwrites such a key without `force`.
  Every change is in `app_config_history` (GET `/api/admin/config/{key}/history`).
- Chapter sections: a non-seed revision (admin editor, assistant) protects the section. When the seed itself
  replaces a body, the previous body is stored as a `seed` revision in `chapter_section_revisions`.
