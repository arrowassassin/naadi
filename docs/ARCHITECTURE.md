# Naadi architecture

How the running system is put together. The product spec is [`handoff/HANDOFF.md`](../handoff/HANDOFF.md); how to
operate it is [`RUNBOOK.md`](RUNBOOK.md); its security model is [`SECURITY.md`](SECURITY.md).

## The picture

```
 iPad / phone / laptop browser ── installable PWA (service worker, Dexie device copy + outbox)
          │ HTTPS, one origin (Hugging Face's proxy terminates TLS)
          ▼
 ┌─ Hugging Face Docker Space "naadi" (cpu-basic, one container, uid 1000, :7860) ─────────────────────┐
 │ start.sh                                                                                               │
 │  ├─ SearXNG (granian, 127.0.0.1:8888, background; search layer 8, off for students until enabled)   │
 │  ├─ migrate_all: Aegra's migrations → Naadi's (advisory-locked) → RLS on Aegra's tables → tripwire     │
 │  └─ exec uvicorn naadi_api.agent.host:app  (ONE worker: Aegra's broker and cron scheduler are in it)   │
 │                                                                                                        │
 │  ASGI stack, outermost first:                                                                          │
 │   RequestContextMiddleware   request id, security headers (CSP, TT report-only, HSTS…), JSON access log │
 │   ClientFloorMiddleware      600 req/min per client IP (proxy-appended X-Forwarded-For entry)          │
 │   AgentRateLimit             Aegra routes: 600 req/min and 30 new runs/min per credential              │
 │   Aegra (Agent Protocol)     /threads /runs /assistants /store /crons  ── LangGraph graphs             │
 │   Naadi FastAPI app          /api/*  (every operation behind a per-user or per-IP bucket)              │
 │   SPA fallback               /  /learn …  (built Vite app from static/, hashed /assets immutable)      │
 └────────────────────────────────────────────────────────────────────────────────────────────────────────┘
          │ Postgres (TLS, pooler)                 │ Storage API                 │ outbound HTTPS only
          ▼                                        ▼                             ▼
  Supabase Free: Postgres 17 + pgvector    Supabase Storage buckets      OpenRouter :free / NVIDIA free models
  (RLS on every public table), Auth        content-public (public),      (only through naadi_api/llm.py),
  (JWT, JWKS)                              content-private, user-uploads, Tavily (guarded, cached), PubMed,
                                           vault, generated-media         Europe PMC, MedlinePlus, openFDA,
                                                                          ClinicalTrials.gov, YouTube Data API,
                                                                          naadi-gen Space, private HF dataset
  GitHub Actions: CI (lint, types, unit, db, e2e 3 browsers × 6 viewports × 3 shards, audit, SBOM),
  weekly restore test, daily live health ping (keeps Supabase from pausing), deploy on green main.
```

## Processes

| Process | Where | What it does |
|---|---|---|
| uvicorn (1 worker) | Space | Aegra hosting the Naadi app (`naadi_api/agent/host.py`); all HTTP traffic |
| Aegra cron scheduler | inside uvicorn | System crons (below), claimed per firing, owned by the system identity |
| Aegra in-memory broker | inside uvicorn | Streams runs to clients; this is why there is exactly one worker |
| SearXNG (granian) | Space, loopback | Self-hosted meta search for layer 8; restarted by start.sh if it exits |
| Background tasks | inside uvicorn | Embedding reconcile on startup, ink tombstone GC, export GC, seed jobs |
| Browser service worker | device | App shell + assets offline; Web Push (S16/S18) |
| Dexie outbox | device | Offline writes (notes, ink, ratings, plan items, pathway) replayed with last-writer-wins |

### System crons (UTC, `SYSTEM_CRONS` in `naadi_api/agent/host.py`)

| Key | Graph | Schedule | Purpose |
|---|---|---|---|
| `naadi.ping.hourly` | ping | `17 * * * *` | Agent runtime self-test → `agent_health` |
| `naadi.tavily_usage.daily` | tavily_usage | `43 3 * * *` | Reconcile Tavily's usage with our ledger |
| `naadi.coach.nightly` | coach | `7 21 * * *` | Study suggestions for Today (02:37 IST) |
| `naadi.youtube_map.weekly` | youtube_map | `17 5 * * 0` | Refresh/drop video metadata (30-day policy), queue candidates |
| `naadi.ink_ocr.sweep` / `.nightly` | ink_ocr | `*/2 * * * *`, `23 0 * * *` | Handwriting search |
| `naadi.author.nightly` / `.psychometrics` | author | `41 22 * * *`, `11 23 * * *` | Question drafts into human review; item statistics |
| `naadi.planner.reminders` | planner | `*/15 * * * *` | Daily Web Push reminders in each student's local time |
| `naadi.pathway_verify.weekly` | pathway_verify | `13 4 * * 1` | Flag pathway tasks whose official page changed |
| `naadi.backup.nightly` | backup | `0 3 * * *` | Encrypted database dump to the private dataset |
| `naadi.backup.storage_weekly` | backup | `30 3 * * 0` | Encrypted Storage tarball |
| `naadi.prune.daily` | prune | `11 4 * * *` | Retention pass (see Budgets) |

## Data flow

- **Reading:** `GET /api/chapters/{id}` returns TipTap JSON from `chapter_sections`; `<ContentRenderer>` maps each node
  to a component; figures resolve to `media_assets` rows whose objects sit in `content-public`. The app bundle ships no
  content (HANDOFF §7.5).
- **Practice:** blocks and attempts are API-written; a question's key and explanation are returned only after an
  attempt is recorded (answer columns are revoked from the client roles; `test_only_the_qbank_and_the_seed_read_answer_columns`
  pins which modules may read them). Exam mode runs on the server clock and disables the assistant.
- **Cards:** FSRS-6 scheduling on the server (`card_reviews`, `card_review_logs`), golden vectors shared with the client.
- **Search:** local hybrid search (fastembed bge-small `halfvec(384)` + pg_trgm, reranked) first; then the layered web
  search in `naadi_api/search.py` (PubMed → Europe PMC → MedlinePlus → openFDA → ClinicalTrials.gov → Tavily (guarded) →
  SearXNG), every result cached in `search_cache`, every query PII-scrubbed.
- **Assistant and jobs:** the web app talks to Aegra over the Agent Protocol (`@langchain/langgraph-sdk`, loaded only
  when used). Graphs call models only through `naadi_api/llm.py`, which applies provider privacy settings, fallbacks,
  per-provider rpm buckets, the daily cap split (chat 70 % / jobs 30 %) and at most 4 calls per turn, and strips PII.
- **Ingest:** a file, link or paste becomes one background `ingest` run: sniff and secure, local sensitivity gate, text
  extraction, chunks embedded privately (`owner_user_id`), placements proposed for the student to confirm.
- **Offline:** reads come from the device copy; writes queue in the Dexie outbox with a client timestamp and replay with
  last-writer-wins (`sync_ops` makes replays idempotent for 24 h, `sync_tombstones` keeps deletes 90 days).
- **Backups:** see RUNBOOK "Backups and restore".

## Agent graphs (`apps/api/aegra.json`)

`ping`, `tavily_usage`, `coach`, `youtube_map`, `author`, `pathway_verify`, `ink_ocr`, `planner`, `ingest`, `backup`,
`prune`, plus the tutor (segment 14). Every graph checks the run identity first: system crons run as the system identity,
admins may run the operational graphs by hand, students only the graphs meant for them. Checkpoints use Aegra's
Postgres saver with a 30-day TTL (`checkpointer.ttl.default_ttl` = 43200 minutes, swept hourly).

## Budgets

| Resource | Limit | How it is held |
|---|---|---|
| Postgres | 500 MB (Supabase Free) | halfvec embeddings; files in Storage; prune: `llm_usage` 90 d, `search_cache` past its longest TTL (720 h), `sync_ops` 24 h, `sync_tombstones` 90 d, `agent_health` 90 d, `search_usage` 13 months; checkpoints 30 d (Aegra) |
| Storage | 1 GB | per-file caps at upload (ingest, Anki 100 MB); generated media is short clips/images |
| LLM calls | 50/day free (1,000 after the one-time credit) | `config/models.yaml` caps, enforced in `llm.py` (`llm_usage` ledger) |
| Tavily | 1,000 credits/month | per-user daily cap + stop at 950, 7-day cache |
| YouTube Data API | 10,000 units/day | weekly mapper uses list calls (1 unit), never `search.list` |
| API requests | per user and per IP | see SECURITY.md "Rate limits" |
| Web bundle | initial JS ≤ 250 KB gz, CSS ≤ 40 KB, page chunk ≤ 150 KB | `pnpm --filter @naadi/web size` (`.size-limit.cjs`) |
| Page speed | Lighthouse ≥ 90 / 100 / ≥ 90; Today interactive < 2 s | `e2e/lighthouse.spec.ts` (iPad, Slow 4G, 4× CPU) |
| API latency | `/api/today` p95 < 1.5 s warm; local search p95 < 300 ms | `tests/embeddings/test_perf.py` in CI; Space check in RUNBOOK |

## Code map

```
apps/api/naadi_api/
  main.py            app factory: routers, buckets, middleware order
  agent/             Aegra host, auth handler, identity, graphs/, limits (protocol rate limits)
  security.py        CSP, Trusted Types report-only, sandbox policies, other headers
  csp_report.py      violation reports (summaries only)
  auth*.py           JWT verification (JWKS), account directory, rate-limit helpers
  llm*.py scrub.py   the only way to call a model; privacy and budgets
  search*.py         layered search + routes; embeddings/ local index
  ops/               backup, restore, prune (segment 22)
  health/            /api/health components (database … backup, searxng)
  <feature>/         courses, qbank, cards, plan, notes, ink, ingest, pathway, export, anki, youtube, admin, …
apps/web/src/
  main.tsx           installs the Trusted Types policies, then the router
  router.tsx         route tree; feature route modules lazy-load their pages (shell/lazyPage.ts)
  content/           ContentRenderer, SafeHtml (allowlist renderer), media nodes
  security/          Trusted Types policies
deploy/space/        Dockerfile (web build, SearXNG stage, API), start.sh
deploy/searxng/      SearXNG settings
```
