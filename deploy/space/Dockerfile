# syntax=docker/dockerfile:1.7
# Naadi single Docker Space: one container serves the built SPA (/) and the API (/api/*) on :7860.
# Build context is assembled by scripts/deploy_space.sh (tracked files only: never .env or secrets).

# ── Stage 1: build the web app (Node 24 LTS + pnpm 12.21.0, Vite 8) ─────────────────────────────
FROM node:24.22.3-slim AS web
ENV CI=1 COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@12.21.0 --activate
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY apps/web/package.json apps/web/
COPY packages/ui/package.json packages/ui/
COPY packages/shared/package.json packages/shared/
RUN pnpm install --frozen-lockfile --filter "@naadi/web..."
COPY packages ./packages
COPY apps/web ./apps/web
# Public build-time config (NOT secrets): HF passes Space *variables* as build args.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
RUN pnpm --filter @naadi/web build

# ── Stage 2: SearXNG for search layer 8 (segment 22), pinned to one commit, its own venv ──────────
# Off for students until app_config.search.layers.searxng.enabled is set (docs/RUNBOOK.md "SearXNG"). The commit
# is checked after checkout; SearXNG's own requirements files pin every package version.
FROM python:3.13.18-slim AS searxng
ARG SEARXNG_COMMIT=ffa88e3ba3ade64185d5754c839cdf5a80708c4a
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --filter=blob:none --no-checkout https://github.com/searxng/searxng.git /opt/searxng/src \
    && git -C /opt/searxng/src checkout --quiet "$SEARXNG_COMMIT" \
    && test "$(git -C /opt/searxng/src rev-parse HEAD)" = "$SEARXNG_COMMIT" \
    && rm -rf /opt/searxng/src/.git /opt/searxng/src/client /opt/searxng/src/docs /opt/searxng/src/tests
RUN python -m venv /opt/searxng/.venv \
    && /opt/searxng/.venv/bin/pip install --no-cache-dir -r /opt/searxng/src/requirements.txt \
       -r /opt/searxng/src/requirements-server.txt

# ── Stage 3: API + static files (Python 3.13, FastAPI) ──────────────────────────────────────────
FROM python:3.13.18-slim
# pg_dump/pg_restore 17 for the nightly backup (segment 22; Debian trixie's PostgreSQL 17 = Supabase's major).
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /home/user/app
# Hash-locked export of apps/api/uv.lock (CI fails if it drifts).
COPY --chown=user apps/api/requirements.txt ./requirements.txt
RUN pip install --require-hashes --no-deps -r requirements.txt
COPY --chown=user apps/api/naadi_api ./naadi_api
COPY --chown=user apps/api/migrations ./migrations
COPY --chown=user apps/api/aegra.json ./aegra.json
COPY --chown=user apps/api/VERSION ./VERSION
COPY --chown=user config ./config
COPY --chown=user data ./data
COPY --chown=user --from=web /src/apps/web/dist ./static
COPY --from=searxng /opt/searxng /opt/searxng
COPY --chown=user deploy/searxng/settings.yml ./deploy/searxng/settings.yml
COPY --chown=user deploy/space/start.sh ./start.sh
ENV NAADI_STATIC_DIR=/home/user/app/static NAADI_CONFIG_DIR=/home/user/app/config \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    NAADI_SEED_ON_START=1
USER user
# Local search (segment 07): download the embedding and reranker models (ids from config/models.yaml) into the
# image, so a query never touches the network. Serving loads them with local_files_only.
RUN python -m naadi_api.embeddings prefetch
EXPOSE 7860
# Startup (start.sh): SearXNG on 127.0.0.1:8888 in the background (restarted if it exits; NAADI_SEARXNG=off skips
# it), then naadi_api/agent/migrate_all.py: Aegra's Alembic migrations + LangGraph checkpoint/store tables, then
# Naadi's (advisory-locked), then RLS on Aegra's tables and a tripwire that every public table has RLS. A failure
# logs one `migrate_all.failed` line and the container exits, so nothing serves on a half-migrated schema.
# Then uvicorn replaces the shell (exec) and receives signals. Its startup records and runs the deploy seed
# (NAADI_SEED_ON_START: sources, videos, app_config; admin edits kept) in the background; a failure is only
# logged. The top-level app is Aegra hosting the Naadi app (naadi_api/agent/host.py). ONE worker: Aegra's in-memory broker and cron scheduler live in the process, so a
# second worker would split streams from their runs and fire crons twice.
# --no-access-log: our middleware writes one JSON line per request without query strings.
# --log-config: uvicorn's own lines use the same JSON formatter as the app (naadi_api/uvicorn_logging.json).
CMD ["sh", "./start.sh"]
