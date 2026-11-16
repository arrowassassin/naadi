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

# ── Stage 2: API + static files (Python 3.13, FastAPI) ──────────────────────────────────────────
FROM python:3.13.18-slim
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
ENV NAADI_STATIC_DIR=/home/user/app/static NAADI_CONFIG_DIR=/home/user/app/config \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed
USER user
# Local search (segment 07): download the embedding and reranker models (ids from config/models.yaml) into the
# image, so a query never touches the network. Serving loads them with local_files_only.
RUN python -m naadi_api.embeddings prefetch
EXPOSE 7860
# Startup (naadi_api/agent/migrate_all.py): Aegra's Alembic migrations + LangGraph checkpoint/store tables, then
# Naadi's (advisory-locked), then RLS on Aegra's tables and a tripwire that every public table has RLS. A failure
# logs one `migrate_all.failed` line and the container exits, so nothing serves on a half-migrated schema.
# Then uvicorn replaces the shell (exec) and receives signals. The top-level app is Aegra hosting the Naadi app
# (naadi_api/agent/host.py). ONE worker: Aegra's in-memory broker and cron scheduler live in the process, so a
# second worker would split streams from their runs and fire crons twice.
# --no-access-log: our middleware writes one JSON line per request without query strings.
# --log-config: uvicorn's own lines use the same JSON formatter as the app (naadi_api/uvicorn_logging.json).
CMD ["sh", "-c", "python -m naadi_api.agent.migrate_all && exec uvicorn naadi_api.agent.host:app --host 0.0.0.0 --port 7860 --workers 1 --proxy-headers --forwarded-allow-ips '*' --no-access-log --log-config naadi_api/uvicorn_logging.json --timeout-graceful-shutdown 10"]
