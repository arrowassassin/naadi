# ── Stage 1: build the web app (Vite 8 + React 19) ─────────────────────────
FROM node:24-slim AS web
RUN corepack enable && corepack prepare pnpm@12.21.0 --activate
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages ./packages
RUN pnpm install --frozen-lockfile
COPY apps/web ./apps/web
# Public build-time config (NOT secrets) — set as Space *variables*
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_API_BASE=/api
RUN pnpm --filter @naadi/web build

# ── Stage 2: API + static files (Python 3.13, FastAPI, LangGraph) ──────────
FROM python:3.13-slim
RUN useradd -m -u 1000 user                       # HF Spaces run as uid 1000
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user apps/api ./
COPY --chown=user --from=web /src/apps/web/dist ./static
USER user
EXPOSE 7860
CMD ["uvicorn", "naadi_api.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "2", "--proxy-headers"]
