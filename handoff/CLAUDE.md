# CLAUDE.md — Naadi

Read `HANDOFF.md` first. It is the product and engineering spec; this file is the working agreement.

## Repo layout
```
apps/web        Vite 8 + React 19 SPA/PWA (TanStack Router/Query, Tailwind 4, TipTap 3, perfect-freehand, ts-fsrs, Dexie)
apps/api        Python 3.12 (free Gradio/ZeroGPU Space) or 3.13 (Docker/PRO); FastAPI-style + LangGraph (naadi_api/), Alembic migrations, scripts/seed.py
packages/ui     tokens.css (from design/System.dc.html), shared React components
packages/shared zod schemas mirrored by pydantic models (generate one from the other; never hand-drift)
design/         Claude Design boards: visual reference only, never imported
data/ config/   seed content and configs from the handoff bundle
```

## Rules
- iPad Safari is the primary target. Test every UI change at iPad portrait and landscape and in Playwright WebKit before calling it done.
- Follow the colour roles exactly: lime = next action (one per view), violet = assistant, paper = student. Status never relies on colour alone.
- Pin exact versions (see HANDOFF §3). Upgrade deliberately in a dedicated PR with the changelog read.
- Free tier first: only `:free` OpenRouter models, local fastembed embeddings, Tavily via `search_cache`. Respect the budget guards in `config/models.yaml` (rpm, daily cap, max 4 LLM calls per turn); strip PII before any LLM call.
- Model IDs live only in `config/models.yaml`. Call LLMs only through `naadi_api/llm.py`, which applies the provider object (`zdr`, `data_collection: deny`), fallbacks, and caching.
- Never commit secrets. Read them via `naadi_api/settings.py`. Never log request bodies that contain uploads or notes.
- Content legality: every media or text asset references a `sources` row. Don't add copyrighted text (First Aid, UWorld, NBME items) to the repo, seed data, prompts or RAG. Use YouTube only through the official IFrame player.
- Never reveal a question's answer or explanation before the student answers. The assistant is disabled in exam mode.
- DB changes only via Alembic migrations, with RLS policies in the same migration.
- Keep the seed importer idempotent (upsert by id).

## Commands (set these up in M0)
- `pnpm dev`: web on :5173, proxying `/api` to :7860
- `uv run uvicorn naadi_api.main:app --reload --port 7860`: API
- `pnpm test` · `pnpm e2e` (Playwright WebKit + Chromium) · `uv run pytest`
- `uv run python scripts/seed.py --data ../../data`: import seed content
- `uv run python ../../tools/check_models.py`: verify OpenRouter model IDs and prices
- Deploy: push `main`, then CI uploads to the HF Space (see HANDOFF §10; use the huggingface-spaces skill if available)
