# Segment 04 — Agent platform: LLM layer, budget guards, Aegra runtime

**Implementer:** `implementer-opus`. Reason: every later agent depends on this runtime, and it holds the security- and budget-critical code (auth on threads/runs, PII stripping, free-only routing, daily caps).
**Reviewer:** `reviewer-opus` (mandatory).
**Depends on:** 01 (scaffold, health, deploy), 02 (schema: `llm_usage`, `search_cache`, `assistant_threads`, `app_config`), 03 (Supabase JWT verification, admin role).
**HANDOFF refs:** §6 (AI layer), §6.4 (streaming), §6.5 (safety), §9, §13 risks 1, 9, 11. Decisions: `docs/PLAN.md` → "Agent platform", "Agent graphs", "Alembic".

## Goal
A production agent runtime on the Space: Aegra runs Naadi's LangGraph graphs with Supabase auth, per-user isolation, streaming, interrupts, background runs and crons; every LLM and web-search call passes through budget- and privacy-guarded wrappers. One end-to-end `ping` graph proves the whole path on the live Space.

## Scope

### 1. LLM layer — `naadi_api/llm.py` (the only way any code calls a model)
- Routing from `app_config.models` (seeded from `config/models.yaml`; cached 60 s). Model IDs never appear in code.
- Chat models via `langchain-openrouter` (LangGraph-native, tool calling, streaming); non-chat calls (key metadata, model list, embeddings fallback) via the `openrouter` SDK.
- **Free-only:** every primary/fallback ID must end in `:free` or be the free router `openrouter/free`; anything else is rejected at config load and in `check_models.py`.
- Fallbacks in order on 429/5xx/timeouts; the reviewer role records `review_not_independent` when its fallback shares the author's family.
- **Rate:** token bucket at 18 requests/min (process-wide, async-safe).
- **Daily cap:** from `OPENROUTER_DAILY_CAP` and `app_config.budget`; 70 % reserved for the tutor, 30 % for jobs; counts from `llm_usage` (one row per call: user, role, model, ok, status, tokens). Reset at 00:00 UTC.
- **Per turn:** hard stop at 4 LLM calls (`max_llm_calls_per_turn`).
- **Degradation:** when the cap or rate limit is hit, raise a typed `BudgetExhausted` carrying the reset time; graphs answer from course retrieval only and the UI shows "AI quota resets at HH:MM" (copy from `app_config.ui_copy`).
- **PII stripping** before every call: the student's name, email, phone numbers, IDs and anything from `profiles` replaced with placeholders; vault content can never reach this module (enforced by type and by a test that tries).
- PDFs: always `plugins: [{id: "file-parser", pdf: {engine: "cloudflare-ai"}}]`.
- Attribution headers from `OPENROUTER_APP_URL` / `OPENROUTER_APP_TITLE`.
- Never log prompts or completions; log role, model, status, tokens, latency, `request_id`/`run_id`.

### 2. Web search guard — `naadi_api/search.py`
- `tavily-python` only (`search`, `extract`; never `research`/`crawl`/`map`), `basic` depth, ≤ 5 results, `include_domains` from `app_config.search`.
- Normalized-query cache in `search_cache` (7-day TTL); per-user daily cap 25; monthly credit guard stops at 950 and falls back to course retrieval. Credits counted from our own ledger (plus Tavily's usage endpoint over HTTPS for reconciliation in `/api/health`, with a comment why).

### 3. `tools/check_models.py` on the `openrouter` SDK
Already ported in 01; extend to validate `app_config.models` (DB) as well as the YAML, and to fail on paid IDs.

### 4. Aegra runtime (`aegra-api==0.11.0`, Apache-2.0)
- Aegra is the **top-level ASGI process** in the Space. `aegra.json` registers the graphs and hosts our app: `"http": {"app": "naadi_api.main:app"}`. One origin: SPA at `/`, Naadi REST at `/api/*`, Agent Protocol endpoints under a path that does not collide with SPA routes (verify Aegra's mounting options; if its routes must live at the root, reserve `/assistants`, `/threads`, `/runs`, `/store` and make the SPA router treat them as server paths).
- Postgres = Supabase via `DATABASE_URL` (Aegra parses it; check pooler session-mode compatibility with asyncpg and prepared statements). Aegra runs its own Alembic migrations into its own tables; Naadi's use `naadi_alembic_version` (set in 02). Prove both migrate cleanly into an empty database and coexist.
- Broker: in-memory (single container); `REDIS_BROKER_ENABLED=false`.
- **Auth:** custom handler verifying the Supabase JWT (same JWKS path as 03). Every assistant/thread/run/cron/store item is owned by `auth.uid()`; user A cannot read, stream, resume, cancel or list user B's objects. System crons run as a service identity with no access to user-private data unless the graph loads it explicitly for that user.
- Observability: Aegra's request logs merged into our JSON logging with `request_id`/`run_id`; OpenTelemetry exporters off unless configured.
- Checkpoint retention: prune checkpoints older than 30 days (keep final state) via an Aegra cron or our job.
- `/api/health` gains `agent_runtime` (graphs loaded, DB reachable from Aegra, broker ok, crons scheduler alive).
- Dockerfile/entrypoint updated; `deploy_space.sh` unchanged otherwise.

### 5. Clients
- Backend/jobs: `langgraph-sdk` (Python) to create runs (e.g. background `ingest`, `ink_ocr`).
- Web: `@langchain/langgraph-sdk` with the `useStream` React hook, sending the Supabase access token. A thin `useAgent` wrapper in `apps/web` adds the degradation and exam-mode states.

### 6. `ping` graph (proves the platform; kept as the runtime's self-test)
A minimal graph that: streams tokens from the `router` role, emits a custom `block` event, raises an `interrupt()` and resumes on confirm, can run as a background run, and is scheduled as an hourly cron that writes its result to a health table. Exposed on an admin-only diagnostics panel (no student-facing placeholder UI).

## Acceptance criteria
1. Unit: free-only validation, fallback order, rpm bucket, daily cap + 70/30 reserve, 4-calls-per-turn stop, PII stripping (names, emails, phones, profile fields), vault-can't-reach-LLM, Tavily cache hit/miss, per-user cap, 950-credit stop. Recorded fixtures allowed here only.
2. Integration (CI + Space, real services): one real `:free` call through `llm.py` lands an `llm_usage` row; Tavily search is cached on the second call; Aegra + Naadi migrations apply to an empty DB and coexist.
3. Isolation tests: user B gets 404/403 for user A's threads, runs, streams, crons and store items.
4. Live Space: `ping` streams through the Spaces proxy (no buffering), interrupt → resume works, a background run completes, the cron fires and records its result; `/api/health` shows `agent_runtime: ok`.
5. Degradation path visible in the UI when the cap is forced to 0 (e2e, all viewports, axe clean).
6. Nothing model- or budget-related hardcoded in code; all from `app_config` / `config/models.yaml`.

## Screens × states
Admin diagnostics panel (agent runtime + `ping` run): populated, running/streaming, interrupted (awaiting confirm), budget exhausted, error, offline. All viewports.
