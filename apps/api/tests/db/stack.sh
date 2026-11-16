#!/usr/bin/env bash
# Local / CI database stack for the `db` tests: the real Supabase Postgres image plus the real Supabase
# Storage service, which creates and migrates the `storage` schema exactly as on the hosted project
# (the Postgres image alone ships an empty `storage` schema).
#
#   apps/api/tests/db/stack.sh up        # start Postgres (+ Storage) in docker, wait until migrated
#   apps/api/tests/db/stack.sh storage   # Postgres already running (CI service container): add Storage only
#   apps/api/tests/db/stack.sh down      # remove the containers
#
# Env (defaults in brackets): NAADI_PG_CONTAINER [pg-naadi-test], NAADI_PG_PORT [54322],
# NAADI_STORAGE_PORT [unset: Storage is reachable only inside the docker network; set it (e.g. 54335) to publish
# storage-api on localhost for the seed importer's Storage uploads, NAADI_STORAGE_URL=http://localhost:<port>],
# NAADI_STORAGE_CONTAINER [storage-naadi-test], NAADI_PG_HOST for `storage` [localhost], NAADI_PG_PASSWORD
# [postgres]. Every credential here is a throwaway for a local container, never a real secret.
set -euo pipefail

PG_IMAGE="supabase/postgres:17.11.0.019"
STORAGE_IMAGE="supabase/storage-api:v1.95.0"
PG_CONTAINER="${NAADI_PG_CONTAINER:-pg-naadi-test}"
PG_PORT="${NAADI_PG_PORT:-54322}"
PG_HOST="${NAADI_PG_HOST:-localhost}"
PG_PASSWORD="${NAADI_PG_PASSWORD:-postgres}"
STORAGE_CONTAINER="${NAADI_STORAGE_CONTAINER:-storage-naadi-test}"
STORAGE_PORT="${NAADI_STORAGE_PORT:-}"
NETWORK="${NAADI_TEST_NETWORK:-naadi-test-net}"
API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Local-only JWT secret for the throwaway Storage container (it must be >= 32 chars).
TEST_JWT_SECRET="naadi-local-test-only-jwt-secret-0000"

log() { printf '[stack] %s\n' "$*" >&2; }

pg_url() { printf 'postgresql://%s:%s@%s:%s/postgres' "$1" "$PG_PASSWORD" "$PG_HOST" "$PG_PORT"; }

wait_pg() {
  for _ in $(seq 1 90); do
    if uv --directory "$API_DIR" run --frozen python -c "import psycopg,sys; psycopg.connect(sys.argv[1], connect_timeout=3).close()" \
      "$(pg_url postgres)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  log "Postgres did not become ready on ${PG_HOST}:${PG_PORT}"
  return 1
}

# The Storage service connects as supabase_storage_admin; give it the container password (as supabase_admin).
set_storage_password() {
  uv --directory "$API_DIR" run --frozen python - "$(pg_url supabase_admin)" "$PG_PASSWORD" <<'PY'
import sys
import psycopg
from psycopg import sql

with psycopg.connect(sys.argv[1], autocommit=True, connect_timeout=5) as c:
    c.execute(sql.SQL("alter role supabase_storage_admin with login password {}").format(sql.Literal(sys.argv[2])))
PY
}

wait_storage_migrated() {
  # Storage runs its migrations at boot; it is done when storage.objects and the latest helpers exist and
  # the service reports that it is listening.
  for _ in $(seq 1 90); do
    # (captured, not piped into `grep -q`: with pipefail an early grep exit would fail the pipeline)
    local out
    out="$(docker logs "$STORAGE_CONTAINER" 2>&1 || true)"
    if [[ "$out" == *"Started Successfully"* ]]; then
      if uv --directory "$API_DIR" run --frozen python - "$(pg_url postgres)" >/dev/null 2>&1 <<'PY'
import sys
import psycopg

with psycopg.connect(sys.argv[1], connect_timeout=3) as c:
    ok = c.execute(
        "select to_regclass('storage.objects') is not null and to_regclass('storage.buckets') is not null"
        " and to_regprocedure('storage.foldername(text)') is not null"
    ).fetchone()
    sys.exit(0 if ok and ok[0] else 1)
PY
      then
        log "storage schema migrated"
        return 0
      fi
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$STORAGE_CONTAINER" 2>/dev/null)" != "true" ]; then
      log "storage container exited:"
      docker logs "$STORAGE_CONTAINER" 2>&1 | tail -20 >&2
      return 1
    fi
    sleep 2
  done
  log "storage did not finish migrating"
  docker logs "$STORAGE_CONTAINER" 2>&1 | tail -20 >&2
  return 1
}

start_storage() {
  local db_host="$1" net_args=("${@:2}")
  docker rm -f "$STORAGE_CONTAINER" >/dev/null 2>&1 || true
  set_storage_password
  docker run -d --name "$STORAGE_CONTAINER" "${net_args[@]}" \
    -e DATABASE_URL="postgres://supabase_storage_admin:${PG_PASSWORD}@${db_host}/postgres" \
    -e AUTH_JWT_SECRET="$TEST_JWT_SECRET" -e PGRST_JWT_SECRET="$TEST_JWT_SECRET" \
    -e ANON_KEY=local-test-anon -e SERVICE_KEY=local-test-service \
    -e STORAGE_BACKEND=file -e FILE_STORAGE_BACKEND_PATH=/tmp/storage \
    -e TENANT_ID=stub -e REGION=stub -e GLOBAL_S3_BUCKET=stub -e DB_INSTALL_ROLES=false \
    -e FILE_SIZE_LIMIT=52428800 \
    "$STORAGE_IMAGE" >/dev/null
  wait_storage_migrated
}

cmd="${1:-up}"
case "$cmd" in
  up)
    if ! docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
      docker network create "$NETWORK" >/dev/null 2>&1 || true
      log "starting $PG_IMAGE as $PG_CONTAINER on port $PG_PORT"
      docker run -d --name "$PG_CONTAINER" --network "$NETWORK" -e POSTGRES_PASSWORD="$PG_PASSWORD" \
        -p "${PG_PORT}:5432" "$PG_IMAGE" >/dev/null
    else
      docker start "$PG_CONTAINER" >/dev/null
      docker network connect "$NETWORK" "$PG_CONTAINER" >/dev/null 2>&1 || true
    fi
    wait_pg
    if [ -n "$STORAGE_PORT" ]; then
      start_storage "${PG_CONTAINER}:5432" --network "$NETWORK" -p "${STORAGE_PORT}:5000"
      log "storage-api on http://localhost:${STORAGE_PORT} (service key: tests/db/storage.py mints it)"
    else
      start_storage "${PG_CONTAINER}:5432" --network "$NETWORK"
    fi
    log "ready: DATABASE_URL=$(pg_url postgres)"
    ;;
  storage)
    wait_pg
    start_storage "${PG_HOST}:${PG_PORT}" --network host
    ;;
  down)
    docker rm -f "$STORAGE_CONTAINER" "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: $0 up|storage|down" >&2
    exit 2
    ;;
esac
