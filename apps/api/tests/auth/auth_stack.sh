#!/usr/bin/env bash
# Supabase's own auth server (supabase/auth, a.k.a. GoTrue) for CI and local e2e, so sign-in is tested for
# real without any hosted-project secret. It runs against the same supabase/postgres as the db tests
# (apps/api/tests/db/stack.sh) and is published behind a tiny nginx at http://localhost:<port>/auth/v1,
# the path supabase-js and the API expect.
#
#   apps/api/tests/auth/auth_stack.sh up   # local: Postgres container from stack.sh (same network)
#   apps/api/tests/auth/auth_stack.sh ci   # CI: Postgres is a service container on the host network
#   apps/api/tests/auth/auth_stack.sh down
#
# Each `up`/`ci` mints a fresh random HS256 secret and matching anon / service_role JWTs. They are
# throwaway values for a local container, not secrets of the real project; they are never committed.
# Output: env lines SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET,
# written to $GITHUB_ENV (values masked) in CI, else to $NAADI_AUTH_ENV_FILE
# [test-results/.auth-stack.env, mode 600].
#
# Env (defaults in brackets): NAADI_PG_CONTAINER [pg-naadi-test], NAADI_PG_HOST [localhost],
# NAADI_PG_PORT [54322], NAADI_PG_PASSWORD [postgres], NAADI_TEST_NETWORK [naadi-test-net],
# NAADI_AUTH_CONTAINER [auth-naadi-test], NAADI_AUTH_PROXY_CONTAINER [auth-proxy-naadi-test],
# NAADI_AUTH_PORT [54329] (published proxy port), NAADI_AUTH_SITE_URL [http://127.0.0.1:7860],
# NAADI_AUTH_STORAGE_UPSTREAM [unset] host:port of the storage-api from stack.sh (e.g. storage-naadi-test:5000
# locally, 127.0.0.1:5000 in CI): when set, the same gateway also serves `/storage/v1/`, so the browser loads
# public figures from SUPABASE_URL/storage/v1/object/public/... exactly as on the hosted project.
set -euo pipefail

AUTH_IMAGE="supabase/auth:v2.197.0"
PROXY_IMAGE="nginx:1.29.0-alpine"
PG_CONTAINER="${NAADI_PG_CONTAINER:-pg-naadi-test}"
PG_HOST="${NAADI_PG_HOST:-localhost}"
PG_PORT="${NAADI_PG_PORT:-54322}"
PG_PASSWORD="${NAADI_PG_PASSWORD:-postgres}"
NETWORK="${NAADI_TEST_NETWORK:-naadi-test-net}"
AUTH_CONTAINER="${NAADI_AUTH_CONTAINER:-auth-naadi-test}"
PROXY_CONTAINER="${NAADI_AUTH_PROXY_CONTAINER:-auth-proxy-naadi-test}"
AUTH_PORT="${NAADI_AUTH_PORT:-54329}"
SITE_URL="${NAADI_AUTH_SITE_URL:-http://127.0.0.1:7860}"
STORAGE_UPSTREAM="${NAADI_AUTH_STORAGE_UPSTREAM:-}"
API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_DIR="$(cd "$API_DIR/../.." && pwd)"
ENV_FILE="${NAADI_AUTH_ENV_FILE:-$REPO_DIR/test-results/.auth-stack.env}"
PUBLIC_URL="http://localhost:${AUTH_PORT}"

log() { printf '[auth-stack] %s\n' "$*" >&2; }

py() { uv --directory "$API_DIR" run --frozen python "$@"; }

set_auth_password() {
  # The image creates supabase_auth_admin without a usable password; give it the container's (throwaway).
  py - "postgresql://supabase_admin:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/postgres" "$PG_PASSWORD" <<'PY'
import sys
import psycopg
from psycopg import sql

with psycopg.connect(sys.argv[1], autocommit=True, connect_timeout=5) as c:
    c.execute(sql.SQL("alter role supabase_auth_admin with login password {}").format(sql.Literal(sys.argv[2])))
PY
}

mint() {
  # Prints: <secret> <anon jwt> <service_role jwt>
  py - <<'PY'
import secrets
import time

import jwt

secret = secrets.token_urlsafe(48)
now = int(time.time())
def key(role: str) -> str:
    return jwt.encode({"iss": "supabase", "role": role, "iat": now, "exp": now + 7 * 86400}, secret, algorithm="HS256")
print(secret, key("anon"), key("service_role"))
PY
}

storage_location() {
  [ -n "$STORAGE_UPSTREAM" ] || return 0
  cat <<NGINX
    # Supabase's gateway path for Storage (stack.sh's storage-api serves it at the root).
    location /storage/v1/ {
      proxy_pass http://${STORAGE_UPSTREAM}/;
      proxy_set_header Host \$host;
      proxy_hide_header Access-Control-Allow-Origin;
      add_header Access-Control-Allow-Origin \$http_origin always;
    }
NGINX
}

write_proxy_conf() {
  local upstream="$1" conf="$2"
  cat >"$conf" <<NGINX
events {}
http {
  server {
    listen ${AUTH_PORT};
    # CORS as Supabase's API gateway answers it (the auth server itself leaves that to the gateway).
    location /auth/v1/ {
      if (\$request_method = OPTIONS) {
        add_header Access-Control-Allow-Origin \$http_origin always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers \$http_access_control_request_headers always;
        add_header Access-Control-Max-Age 600 always;
        return 204;
      }
      proxy_hide_header Access-Control-Allow-Origin;
      add_header Access-Control-Allow-Origin \$http_origin always;
      add_header Access-Control-Expose-Headers "X-Supabase-Api-Version, X-Request-Id" always;
      proxy_pass http://${upstream}/;
      proxy_set_header Host \$host;
      proxy_set_header X-Forwarded-For \$remote_addr;
    }
$(storage_location)
  }
}
NGINX
}

wait_auth() {
  for _ in $(seq 1 90); do
    if curl -fsS "${PUBLIC_URL}/auth/v1/health" >/dev/null 2>&1; then
      log "auth server ready at ${PUBLIC_URL}/auth/v1"
      return 0
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$AUTH_CONTAINER" 2>/dev/null)" != "true" ]; then
      log "auth container exited:"
      docker logs "$AUTH_CONTAINER" 2>&1 | tail -20 >&2
      return 1
    fi
    sleep 2
  done
  log "auth server did not become ready"
  docker logs "$AUTH_CONTAINER" 2>&1 | tail -20 >&2
  return 1
}

start() {
  local db_host="$1" upstream="$2"
  shift 2
  local net_args=("$@")
  docker rm -f "$AUTH_CONTAINER" "$PROXY_CONTAINER" >/dev/null 2>&1 || true
  set_auth_password
  local secret anon service
  read -r secret anon service < <(mint)

  docker run -d --name "$AUTH_CONTAINER" "${net_args[@]}" \
    -e GOTRUE_API_HOST=0.0.0.0 -e PORT=9999 \
    -e API_EXTERNAL_URL="${PUBLIC_URL}/auth/v1" \
    -e GOTRUE_JWT_ISSUER="${PUBLIC_URL}/auth/v1" \
    -e GOTRUE_DB_DRIVER=postgres \
    -e GOTRUE_DB_DATABASE_URL="postgres://supabase_auth_admin:${PG_PASSWORD}@${db_host}/postgres" \
    -e GOTRUE_SITE_URL="$SITE_URL" \
    -e GOTRUE_URI_ALLOW_LIST="http://127.0.0.1:*/**,http://localhost:*/**" \
    -e GOTRUE_JWT_SECRET="$secret" -e GOTRUE_JWT_EXP=3600 -e GOTRUE_JWT_AUD=authenticated \
    -e GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated -e GOTRUE_JWT_ADMIN_ROLES=service_role \
    -e GOTRUE_DISABLE_SIGNUP=false -e GOTRUE_EXTERNAL_EMAIL_ENABLED=true -e GOTRUE_MAILER_AUTOCONFIRM=true \
    -e GOTRUE_RATE_LIMIT_VERIFY=100000 -e GOTRUE_RATE_LIMIT_TOKEN_REFRESH=100000 \
    -e GOTRUE_RATE_LIMIT_OTP=100000 -e GOTRUE_LOG_LEVEL=warn \
    "$AUTH_IMAGE" >/dev/null

  local conf_dir
  conf_dir="$(mktemp -d)"
  write_proxy_conf "$upstream" "$conf_dir/nginx.conf"
  chmod 755 "$conf_dir"
  chmod 644 "$conf_dir/nginx.conf"
  local proxy_net=("${net_args[@]}")
  if [ "${net_args[1]:-}" != "host" ]; then proxy_net+=(-p "${AUTH_PORT}:${AUTH_PORT}"); fi
  docker run -d --name "$PROXY_CONTAINER" "${proxy_net[@]}" \
    -v "$conf_dir/nginx.conf:/etc/nginx/nginx.conf:ro" "$PROXY_IMAGE" >/dev/null
  wait_auth

  local lines=(
    "SUPABASE_URL=${PUBLIC_URL}"
    "SUPABASE_ANON_KEY=${anon}"
    "SUPABASE_SERVICE_ROLE_KEY=${service}"
    "SUPABASE_JWT_SECRET=${secret}"
  )
  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "::add-mask::${anon}"
    echo "::add-mask::${service}"
    echo "::add-mask::${secret}"
    printf '%s\n' "${lines[@]}" >>"$GITHUB_ENV"
    log "auth env exported to GITHUB_ENV (values masked)"
  else
    mkdir -p "$(dirname "$ENV_FILE")"
    (umask 077 && printf '%s\n' "${lines[@]}" >"$ENV_FILE")
    log "auth env written to $ENV_FILE"
  fi
}

cmd="${1:-up}"
case "$cmd" in
  up)
    docker network connect "$NETWORK" "$PG_CONTAINER" >/dev/null 2>&1 || true
    start "${PG_CONTAINER}:5432" "${AUTH_CONTAINER}:9999" --network "$NETWORK"
    ;;
  ci)
    start "${PG_HOST}:${PG_PORT}" "127.0.0.1:9999" --network host
    ;;
  down)
    docker rm -f "$AUTH_CONTAINER" "$PROXY_CONTAINER" >/dev/null 2>&1 || true
    rm -f "$ENV_FILE"
    ;;
  *)
    echo "usage: $0 up|ci|down" >&2
    exit 2
    ;;
esac
