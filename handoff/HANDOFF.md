# Naadi — build handoff for Claude Code

**Build Naadi: an iPad-first, installable web app that replaces USMLE textbooks for an Indian MBBS graduate.** It combines readings, embedded videos, a question bank, flashcards, ink notes, a planner, an AI tutor on every page, and an A–Z pathway from MBBS to practising in the US.

**Everything runs on free tiers:**
- **Hosting:** a free Hugging Face Space.
- **Database:** Supabase Free (Postgres + pgvector + Auth + Storage).
- **Uploads overflow:** Cloudflare R2 free storage (10 GB).
- **Agent:** LangGraph (Python).
- **Models:** OpenRouter `:free` models only.
- **Embeddings:** run locally on the Space CPU.
- **Web search:** Tavily's free 1,000 credits a month.

The only recommended spend is a **one-time $10 OpenRouter credit purchase**. It raises the free-model cap from 50 to 1,000 requests a day, and free calls never draw the credits down. A paid upgrade path is noted per component.

Prepared 15 Nov 2026. Library versions were checked on npm/PyPI that day. Model IDs come from OpenRouter's catalog and must be re-checked before deploy (§6).

## 0. What's in this bundle (sources of truth)

| Path | What it is | Authority |
|---|---|---|
| `HANDOFF.md` | This spec | Product + engineering |
| `CLAUDE.md` | Drop into the repo root; working rules for Claude Code | Engineering |
| `design/*.dc.html`, `design/README.md` | 27 Claude Design boards + `canvas.json`. The live canvas is https://claude.ai/artifact/YAgT7dYtrwxUQqz3HnXXHa | Visual source of truth. Boards need the canvas runtime (`support.js`), so read them as HTML/CSS reference; don't ship them |
| `design/System.dc.html` | Tokens + components sheet | Start here for styling |
| `naadi-design-spec.md` (project doc) | Product rules, tokens, components, screen inventory | Visual rules (summarised in §4) |
| `db/schema.sql` | Postgres 17 schema with pgvector + RLS | Data contract |
| `data/` | Seed content: 14 chapters, 259 sections, 685 questions, 95 SVG figures, 60-day plan, taxonomy, pathway stages/tasks | Seed data (import in M1) |
| `config/models.yaml` | OpenRouter **free** model routing per role + budget guards | AI config |
| `config/sources.yaml`, `config/youtube_channels.yaml` | Content-source licenses and YouTube allowlist | Legal gates for content |
| `config/authoring_rules.md` | NBME item-writing rules + AI review pipeline | Question quality |
| `config/requirements.api.txt` | Pinned Python deps | Backend |
| `deploy/free-space/` | **Default, $0:** Gradio SDK on the free ZeroGPU flavor, used as a CPU web server via `gradio.Server` (serves SPA + API) | Hosting (default) |
| `deploy/space/` | Single Docker Space (multi-stage Dockerfile); needs a PRO plan | Hosting (if PRO) |
| `deploy/alt-split/` | Static web Space (free) + Docker API Space (PRO) | Hosting (alternative) |
| `tools/` | `export_seed.py`, `map_taxonomy.py`, `youtube_mapper.py` | Content pipeline |
| `research/` | Research notes behind every decision, with source URLs | Evidence |

## 1. Product decisions (closing the design spec's open questions)

| Open question | Decision for v1 | Why |
|---|---|---|
| Single student or multi-user | **Multi-user-ready, single-tenant launch.** Supabase Auth plus RLS from day one; one student account to start; admin role for content review. | Costs nothing extra now, avoids a rewrite later |
| Where content comes from | **Three layers:** (1) Naadi originals (seed book: 685 questions, 12 chapters, 95 figures), grown by the authoring pipeline; (2) embedded or linked third-party media under license gates (YouTube embeds, link-outs); (3) the student's own uploads turned into private chapters. No copying of commercial Qbanks or books. | Legal, scalable, and still "one place" |
| Light theme | **Dark app with paper surfaces** (as designed); a light theme comes later behind a token switch | Matches the design |
| Native iPad app or web | **Web PWA first.** Benchmark Pencil latency on a real iPad in M3; wrap with Capacitor 8 + a PencilKit plugin only if web ink misses the target (§5.3). | Fastest path; one codebase |
| Name | "Naadi" (working name; trademark and domain not checked) | — |

## 2. Architecture

```
iPad / phone / laptop (Safari, Chrome) ─ PWA (installable, offline week)
          │  HTTPS, same origin
          ▼
┌──── Hugging Face Space "naadi" — FREE: Gradio SDK + ZeroGPU flavor (CPU only, gradio.Server) ────┐
│     (if PRO: Docker SDK + cpu-upgrade, same code via deploy/space/)                               │
│  FastAPI-style app                                                                   │
│   ├─ /            → static SPA (Vite build: React 19, TanStack Router/Query)        │
│   ├─ /api/*       → REST (content, attempts, plan, notes, ink, uploads, pathway)    │
│   ├─ /api/chat    → SSE stream from the LangGraph tutor graph                       │
│   └─ /api/jobs/*  → ingestion (PDF → chapter), Anki import/export, embeddings       │
│  LangGraph 1.2 graph (PostgresSaver checkpoints) ── OpenRouter :free LLMs            │
│  fastembed bge-small (local embeddings + rerank)  └─ Tavily free (search, cached 7d) │
└──────────────────────────────────────────────────────────────────────────────────────┘
          │ Postgres (TLS)                      │ object storage
          ▼                                     ▼
   Supabase FREE: Postgres + pgvector,    Supabase Storage (1 GB free: vault, small uploads)
   Auth (50k MAU), RLS on every table     + Cloudflare R2 (10 GB free, $0 egress) for big uploads
   GitHub Actions cron: daily keep-alive ping + nightly pg_dump → private HF dataset repo
YouTube IFrame Player (embeds only) · optional Cloudflare Stream for own 4K video (v2)
```

**Free hosting (default):** per the huggingface-spaces skill, Docker and cpu-basic Spaces need a paid plan. A free personal account (verified email, older than 30 days) can host 2 ZeroGPU Spaces, and only with the Gradio SDK.
- The free path is a Gradio-SDK Space on `--flavor zero-a10g`.
- It uses `gradio.Server` for FastAPI-style custom routes and serves the SPA at `/`. Set `GRADIO_SSR_MODE=false` as a Space variable.
- It has one no-op `@spaces.GPU` function, so no GPU quota is ever used.
- Python 3.12 (ZeroGPU supports 3.10.13 and 3.12.12).
- Don't list `gradio`, `spaces` or `huggingface_hub` in requirements.
- Verify on the first deploy that SSE streams through the proxy. If not, use Gradio's streaming `@app.api` generator for `/api/chat`, or move to the Docker path.
- Free Spaces sleep after inactivity, so first load may cold-start. A daily GitHub Actions ping keeps them warm.

**Why one Space:** Spaces lose their local disk on restart, so the database must live outside them. Serving the SPA and the API from one origin avoids cross-Space CORS: 2026 forum reports show the Spaces proxy stripping `Access-Control-Allow-Credentials` and 503s on Space-to-Space calls. It also halves compute cost. `deploy/alt-split/` is the alternative if you want independent web deploys. In that setup, send the Supabase JWT in an `Authorization` header (no cookies), so CORS needs no credentials.

**Hugging Face constraints (from the huggingface-spaces skill and HF docs):**
- Docker Spaces require a paid plan (PRO for a personal account); Static Spaces are free.
- The container runs as uid 1000 and exposes one port (`app_port`, default 7860).
- Runtime secrets arrive as environment variables. Build-time values use `ARG` (public) or `RUN --mount=type=secret` (secret).
- `/data` is wiped on restart unless a Storage Bucket is mounted. Bucket I/O is slow and object-like, so never put SQLite there.
- cpu-basic sleeps when idle. cpu-upgrade is about $0.03/h (≈ $22/month always on).
- Outbound traffic only on ports 80, 443 and 8080.
- Custom domain: public or protected Space plus a CNAME.

### 2.1 Database: free, and why Supabase

| Option (free tier, Nov 2026) | Postgres storage | Includes | Catch |
|---|---|---|---|
| **Supabase Free (chosen)** | 500 MB per project, 2 projects | pgvector, **Auth (50k MAU)**, Storage 1 GB, 5 GB egress, realtime | Pauses after **7 days with no requests**; no automatic backups |
| Neon Free (fallback) | **1 GB per project** (doubled Oct 2026), up to 100 projects | pgvector, branching, scale-to-zero (no pause) | No auth or storage; 100 CU-hours/month per project; cold starts after 5 min idle |
| Cloudflare R2 Free (object storage add-on) | 10 GB stored, 1M writes, 10M reads/month | S3 API, **$0 egress** | Objects only; bills above the free allowance |

Sources: [Supabase pricing](https://supabase.com/pricing), [Neon free-plan limits](https://neon.com/faqs/free-plan-limits-and-quotas), [Neon changelog 2026-10-02](https://neon.com/docs/changelog/2026-10-02), [Cloudflare R2](https://www.cloudflare.com/r2/).

**Why Supabase:** it puts Postgres + pgvector, auth and file storage in one free project, so there's no extra auth service to run.

**Mitigations for its free-tier limits:**
1. **Pause:** a GitHub Actions cron (free) calls `/api/health` daily. That endpoint runs `select 1`, so the project never idles for 7 days.
2. **No backups:** a nightly GitHub Action runs `pg_dump` and pushes the compressed dump to a **private HF dataset repo** (free) or R2, keeping 30 days.
3. **500 MB budget:**
   - Store embeddings as `halfvec(384)` (bge-small), about 0.8 KB per chunk, so 100k chunks ≈ 80 MB plus index.
   - Keep large files out of Postgres: PDFs and videos go in R2, the vault in Supabase Storage.
   - Prune LangGraph checkpoints older than 30 days (keep the final state).
   - Prune `llm_usage` older than 90 days.
   - Expected size at v1: seed content ≈ 20 MB, 6k questions ≈ 30 MB, embeddings ≈ 100 MB. That fits.
4. **Outgrowing it:** move Postgres to Neon Free (1 GB) or Supabase Pro ($25). Same SQL; only `DATABASE_URL` changes. Keep auth on Supabase.

## 3. Stack — latest stable versions (checked 2026-11-15)

**Web (`apps/web`)**

| Concern | Package | Version |
|---|---|---|
| Language / runtime | typescript · node (build) | 7.0.2 (fall back to 5.9 if a tool breaks) · 24 LTS |
| Package manager / monorepo | pnpm workspaces | 12.21.0 |
| Build | vite · @vitejs/plugin-react | 8.3.8 · 6.3.0 |
| UI | react · react-dom | 19.3.1 |
| Routing / data | @tanstack/react-router · @tanstack/react-query | 1.170.46 · 5.107.1 |
| State | zustand | 5.1.0 |
| Styling | tailwindcss · @tailwindcss/vite | 4.3.5 |
| Primitives / icons / motion | @radix-ui/react-dialog (and siblings) · lucide-react · motion | 1.3.1 · 1.73.0 · 14.14.0 |
| Rich text (notes, chapter editor) | @tiptap/react · starter-kit · pm | 3.34.0 |
| Ink | perfect-freehand (+ custom Pointer Events canvas) | 1.2.3 |
| PDF view / export | pdfjs-dist · pdf-lib | 6.5.438 · 1.17.1 |
| Diagrams / math / code | mermaid · katex · shiki | 12.3.0 · 0.19.1 · 4.6.0 |
| Markdown in chat | react-markdown · remark-gfm · rehype-katex | 10.1.0 · 4.0.1 · 7.0.1 |
| Spaced repetition | ts-fsrs (FSRS-6, MIT) | 6.0.0 |
| Offline | vite-plugin-pwa (Workbox) · dexie (IndexedDB) | 2.0.0 · 4.4.7 |
| LangGraph client (optional) | @langchain/langgraph-sdk | 1.16.0 (not needed with our own SSE endpoint) |
| Auth / storage client | @supabase/supabase-js | 2.119.0 |
| Validation | zod | 4.6.7 |
| Native wrapper (only if needed) | @capacitor/core · @capacitor/ios | 8.6.2 |
| Tests / lint | @playwright/test · vitest · @axe-core/playwright · @biomejs/biome | 1.65.1 · 5.1.0 · 4.14.0 · 2.6.8 |

Avoid **tldraw 5.6.0**: it shows a watermark unless you buy a Business License. Excalidraw 0.18.1 (MIT) is fine for the free-form whiteboard inside notebooks, but chapter ink uses our own perfect-freehand layer.

**API (`apps/api`, Python 3.13)** — pinned in `config/requirements.api.txt`:

| Package | Version |
|---|---|
| fastapi · uvicorn[standard] · pydantic · sse-starlette | 0.163.1 · 0.54.0 · 2.14.3 · 3.7.0 |
| langgraph · langgraph-checkpoint-postgres · langchain-core | 1.2.22 · 3.2.1 · 1.8.1 |
| langchain-openai (OpenRouter via `base_url`) · langchain-tavily | 1.8.0 · 0.2.18 |
| psycopg[binary,pool] · pgvector · sqlalchemy · alembic | 3.3.9 · 0.5.1 · 2.1.8 · 1.20.1 |
| pypdf · pymupdf · python-multipart · httpx · huggingface_hub | 7.1.0 · 2.1 · 0.0.33 · 0.28.1 · 2.4.0 |

Free-tier additions: `fastembed` 0.9.1 (local embeddings: BAAI/bge-small-en-v1.5, 384 dims, plus a MiniLM reranker), `faster-whisper` 1.2.1 (optional server STT), and `@huggingface/transformers` 4.3.2 in the browser (Whisper on WebGPU, iPadOS 26). On the free Gradio path, Python is 3.12 and `gradio` 6.32.0 / `spaces` 0.52.0 are platform-managed.

Also available: `langchain-openrouter` 0.2.9 (a dedicated provider package). Either it or `ChatOpenAI(base_url="https://openrouter.ai/api/v1")` works; keep one wrapper in `naadi_api/llm.py`. Self-host LangGraph inside FastAPI. Do **not** use the LangGraph Agent Server: it is Elastic-2.0 licensed, needs a license key, Redis and Postgres, and doesn't fit one Space.

## 4. Design system (from the design spec, binding)

- **Colour roles are never mixed.** Lime `#C6FF3D` = the student's next action (one primary per view). Violet `#9D8CFF` / ground `#17132E` = anything the assistant wrote or does. Paper `#F4F4F0` = anything the student wrote or drew. Amber `#FFB454` = weak area or wrong answer.
- **Ground and text:** ground `#050506`, surfaces `#0E0F12` / `#1B1D22`, lines `#26282E`, text `#F4F4F0` / `#C9CBD1` / labels `#9C9EA6` (the minimum for small text).
- **Highlights have fixed meanings:** lime = key fact, violet = formula, amber = my mistake.
- **Type:** Bricolage Grotesque (display/headlines), Inter (reading 17–20 px, line-height 1.6–1.7; UI 13–16), JetBrains Mono (labels, uppercase). Caveat appears only as a design stand-in for ink.
- **Shape and size:** radius 12 / 16–22 / 26–32 / 36–40 / 999 for controls. Spacing 4–64. Touch targets ≥ 44 px (48 in the assistant bar, 52–60 for primary CTAs).
- **Breakpoints:** phone < 640, tablet 640–1100, laptop > 1100. Max content width 1320.
- **Motion:** 200 ms controls, 400 ms sheets, ease-out, all off under `prefers-reduced-motion`. Focus ring is 3 px violet with a 2 px offset.
- **Status is never colour-only:** every chip carries a word.
- **States the design doesn't cover** (build them from the tokens): hover, loading (skeletons), error, empty, offline.

Port the tokens into `packages/ui/tokens.css` as CSS variables plus Tailwind `@theme`. Components to build first: Nav pill (laptop/iPad), bottom tab bar with centre assistant button (phone), assistant bar, assistant answer blocks, Tile, Buttons, Chips, Selection toolbar, Ink toolbar, Video player.

## 5. iPad-first requirements

### 5.1 Targets
- iPadOS 18.2+ Safari and home-screen PWA (iPad Pro/Air with Apple Pencil 2/Pro) is the **primary** target. Secondary: iPhone Safari, macOS Safari/Chrome, Windows/Android Chrome.
- Layouts are tested at iPad portrait 820×1180, landscape 1180×820, Split View ⅓ and ½, Stage Manager windows, iPhone 390×844, and laptop 1440×900.
- **Pointer handling:** pen draws; one finger scrolls; two fingers zoom or pan the page. Hardware keyboard shortcuts: ⌘K search, 1–5 or A–E answer, F flag, N next, ⌘↵ submit.

### 5.2 Offline
- Install prompt plus `navigator.storage.persist()` on first launch from the home screen. Safari can evict unpersisted data. An origin can use up to about 60% of disk.
- "Download this week" caches the planned chapters, figures, questions and cards in IndexedDB (Dexie), plus own videos at 720p/1080p only. YouTube can't be cached (policy).
- Writes go to an outbox queue (attempts, notes, ink, card reviews) and sync on reconnect, last-writer-wins per row, with ink merged by stroke id.

### 5.3 Ink (Apple Pencil)
- Each surface has a `<canvas>` layer over the content (chapter section, question, flashcard, PDF page, notebook page, exam scratch). Use `touch-action: none` only while the pen tool is active.
- **Palm rejection:** draw only when `pointerType === "pen"`; ignore `touch` while a pen is down.
- Use `getCoalescedEvents()` for full 240 Hz input and `getPredictedEvents()` (Safari 18.2+) for latency. Known WebKit quirks: an 18.2 coalesced-pointerId bug, and `twist` reads 0 on 26.4.1. Pencil hover fires without tilt or roll; use it for the cursor preview only.
- Render strokes with perfect-freehand (pressure → width). Store vector points `[x, y, pressure, t]` in surface-relative coordinates (`ink_layers.strokes`) so ink survives reflow and zoom. Text-anchored margin notes store a ProseMirror position.
- **Tools:** pen, marker (multiply blend), eraser (stroke and pixel), lasso (move/copy/convert), shapes (auto-straighten), text box, sticky/image, two ink colours, "Convert to text", "Circle to ask Naadi". Lasso sends a cropped PNG plus surrounding text to the assistant (vision role).
- **Handwriting search:** send stroke bitmaps to the vision model in a background job to fill `ink_layers.recognized_text`. Apple Scribble works natively in every text input.
- **Performance budget:** pen-to-pixel ≤ 20 ms perceived on iPad Pro (measure with a 240 fps slow-motion video in M3). Keep 60/120 fps scroll with 5k strokes on a page. If the target is missed, plan Capacitor + PencilKit for the ink surfaces only.

### 5.4 Video
- YouTube uses the IFrame Player API, unmodified, with no overlay hiding YouTube branding. Support timestamped notes (`notes.anchor_pos = {t}`), chapter markers from our own `section_media.start_s/end_s`, and speed control.
- **Own video (v2):** Cloudflare Stream ($5 per 1,000 min stored, $1 per 1,000 min delivered; confirm 4K output) or Mux (4K at $0.0096/min stored, $0.0032/min delivered).

## 6. AI layer (LangGraph + OpenRouter + Tavily)

### 6.1 Models — all free (`config/models.yaml`)

| Role | Primary (`:free`) | Free fallbacks | Why |
|---|---|---|---|
| Tutor chat (tools) | nvidia/nemotron-3-ultra-550b-a55b:free (1M ctx) | google/gemma-4-31b-it:free, inclusionai/ling-3.2-flash:free, openrouter/free | Best published free scores (GPQA-D 86.7%, MMLU-Pro 85.8%); tools |
| Vision: photos, handwriting, page images | nvidia/nemotron-3.5-super-vl:free | gemma-4-31b-it:free, ling-3.2-flash:free | Image/doc/video input, structured outputs |
| Structured output (cards, plans, JSON) | inclusionai/ling-3.2-flash:free | baidu/ernie-5.5-flash-preview:free, nemotron-3.5-super-vl:free | Advertises `structured_outputs`; validate with Pydantic |
| Long documents | nvidia/nemotron-3.5-super-vl:free | ling-3.2-flash:free, ernie-5.5-flash-preview:free | 1M ctx; best long-context evidence |
| Question authoring (night jobs) | nvidia/nemotron-3-ultra-550b-a55b:free | ernie-5.5-flash-preview:free | Strongest reasoning |
| Independent reviewer | google/gemma-4-31b-it:free | ernie-5.5-flash-preview:free, ling-3.2-flash:free | Different family from the NVIDIA author |
| Router / guard | Python rules first; then liquid/lfm-3-1.7b:free | nvidia/nemotron-3.5-lightning:free | Every LLM call counts against the daily cap |
| Embeddings | **Local** fastembed `BAAI/bge-small-en-v1.5` (384d, MIT) + MiniLM reranker | nvidia/nemotron-3.5-embed-1b:free (separate index, non-PII only) | No quota, private, runs on the Space CPU |
| Speech-to-text | iPadOS dictation, then Whisper-base in the browser (transformers.js, WebGPU) | faster-whisper base.en on CPU; nemotron-3-nano-omni:free last | All on-device or self-hosted |
| Web search | Tavily free: basic depth, ≤ 5 results, domain filters, 7-day cache | — | 1,000 credits/month ≈ 33 searches a day |

**Free-tier rules the code must enforce** (OpenRouter limits, Nov 2026):

| Rule | Detail |
|---|---|
| Rate | 20 requests/minute on `:free` models. Use a token bucket at 18/min. |
| Daily cap | 50 requests/day account-wide until lifetime credit purchases reach $10, then 1,000/day. **Make the one-time $10 purchase**; free calls don't consume it. |
| Quota visibility | `GET /api/v1/key` returns `free_model_daily_requests`. Log every call in `llm_usage`. |
| Graceful degradation | Reserve 70% of the cap for the tutor; cap the tool loop at 4 LLM calls per turn. When the cap is reached, answer from course retrieval only and show when the quota resets. |
| Fallbacks | Fallback arrays move on 429s and outages, but the daily cap is account-wide. Keep chains free-only: a paid ID bills at its paid price. |
| PDFs | Always send `plugins: [{id: "file-parser", pdf: {engine: "cloudflare-ai"}}]`. The default engine (mistral-ocr) is paid. Better: rasterize scanned pages in the browser and send images. |
| **Privacy** | NVIDIA free endpoints log prompts. Strip names, emails and IDs before every call, and never send vault documents. Don't set `zdr` / `data_collection: deny` on free chains: no eligible provider would remain. |
| Latency | Free endpoints show 1.5–14 s P50 latency. Always stream, and show a "thinking" state. |

Medical leaderboards don't help choose: no free model has a published MedQA score. Ship `tools/model_eval/`: 100 held-out seed questions plus 30 tutor prompts graded by rubric. Run it on each free model before switching roles; it costs only free calls.

The models came from OpenRouter's live catalog read on 15 Nov 2026. `tools/check_models.py` must confirm the IDs at startup and in CI, because free models come and go. **Paid upgrade path:** swap per role in `models.yaml` only (e.g., tutor → anthropic/claude-sonnet-5.5).

### 6.2 Tutor graph (`naadi_api/agent/graph.py`)

```
START → guard (Python rules first — exam mode, answer-leak check, intent; small free model only if ambiguous) ─┬─ exam_mode → refuse_politely → END
                                                               └─ tutor (tool-calling loop) ⇄ tools → compose → END
```

**State:** `messages`, `page_context`, `user_id`, `exam_mode`, `attachments[]`, `citations[]`, `blocks[]`.

**Tools** (each a typed function; the server checks the user):

| Tool | Does | Writes? |
|---|---|---|
| `search_course(query, k)` | Hybrid pgvector + tsvector search over sections, questions and the user's notes/uploads (only `allow_ai_ingest` sources) | No |
| `get_page(ref)` | Loads the current section, question (after answering), or video transcript | No |
| `web_search(query)` | Tavily free (basic, ≤ 5 results, domain bias); checks `search_cache` first; only when course retrieval scores low | No |
| `web_extract(url)` | Tavily extract for one page | No |
| `make_flashcards(items[])` | Creates cards from a selection or answer | **Yes** (asks the user to confirm via a UI block) |
| `add_to_plan(item)` | Planner item | **Yes** (confirm) |
| `save_note(anchor, body)` | Note pinned to the page | **Yes** (confirm) |
| `render_diagram(mermaid)` | Validates the Mermaid source server-side, returns a block | No |
| `suggest_video(topic)` | Approved YouTube or own video from `media_assets` | No |
| `analyze_upload(upload_id)` | Vision/long-context summary of an uploaded file | No |

Write tools use LangGraph `interrupt()` so the UI shows a confirm chip, which keeps assistant writes violet until the student accepts. Checkpointing uses `PostgresSaver` (same database); `thread_id` = `assistant_threads.id`.

### 6.3 Page-context contract (frontend → `/api/chat`)
```json
{"thread_id":"…","message":"…","attachments":[{"upload_id":"…"}],
 "page":{"route":"/learn/ch05/s03","type":"section","id":"ch05-s03","title":"Respiratory physiology",
         "selection":"…highlighted text…","video":{"media_id":"yt:…","t":742},
         "question":{"id":"ch05-q12","answered":true,"chosen":"B"},"exam_mode":false}}
```
Never reveal a question's answer before the student has answered. The guard enforces this, and `get_page` withholds the explanation fields.

### 6.4 Streaming protocol (SSE `text/event-stream`)
Events: `token` (text delta), `block` (`{type: "flowchart"|"image"|"video"|"sources"|"actions"|"card_preview"|"table", data}`), `tool` (status line), `interrupt` (confirm request), `citation`, `done`, `error`. The frontend maps blocks to the "Assistant answer blocks" component. Use LangGraph `stream_mode=["messages","custom","updates"]` and emit custom events from tools.

### 6.5 Safety and quality rules (system prompt + guard)
1. Educational use for exam prep, not patient care. Show a one-line disclaimer on the first answer of each thread.
2. Cite: every web-derived claim carries a source chip. Prefer US guideline sources (USPSTF, CDC, ACC/AHA, ADA, ACOG, AAP, IDSA).
3. Never reproduce copyrighted text (First Aid, UWorld, NBME items, lyrics). Summarise instead.
4. Silent in practice-exam mode: the assistant bar is disabled and the guard refuses.
5. Pathway or visa questions: answer from `pathway_tasks` (with `last_verified`) plus Tavily on official domains, and always link the official page.
6. Don't invent model or drug names. Say "I don't know" and offer a search.

### 6.6 Costs
- **$0 a month** on the free path: free HF Space, Supabase Free, R2 free tier, `:free` models, local embeddings, Tavily free, GitHub Actions free minutes.
- **One-time $10** OpenRouter credit purchase to lift the daily cap to 1,000 requests. That's roughly 250–300 tutor turns a day for one student, plenty.
- **Limits, not bills, are what you hit first:** 1,000 LLM requests a day, 33 web searches a day, and the 500 MB database.
- **Upgrades when needed:**
  - HF PRO (Docker, always-on cpu-upgrade ≈ $22/month)
  - Supabase Pro ($25/month)
  - paid models per role
  - Tavily pay-as-you-go ($0.008 per credit)

## 7. Content system ("Naadi Library") — replacing books

### 7.1 Layers and license gates
Every asset references a `sources` row (`config/sources.yaml`) with `allow_copy / allow_modify / allow_commercial / allow_ai_ingest`. The pipeline **blocks** publishing in three cases: an asset with no license; NC/ND content on paid pages; non-AI-ingest content entering RAG.

| Tier | Examples | Site may |
|---|---|---|
| Naadi original | Seed book (685 questions, 12 chapters, 95 figures); future authored content | Everything |
| Public domain (US gov) | Federal MedlinePlus pages, CDC/USPSTF/AHRQ guidance (verify per document), CDC PHIL (per image) | Copy, adapt, credit the source |
| Copy with attribution | CC BY / CC BY-SA files (Wikimedia Commons per file) | Copy with an attribution line; SA keeps its license |
| Embed or link only | YouTube; StatPearls (CC BY-NC-ND 4.0); OpenStax (now CC BY-NC-SA, and **no AI ingestion**); Radiopaedia (CC BY-NC-SA 3.0); Merck Manual; USMLE samples; NBME lab-values table (© NBME) | Embed or link; no copying into our text or RAG |

**YouTube policy constraints** (Developer Policies):
- No downloading or re-hosting, and no altering or hiding the player or ads.
- No nested-iframe hiding.
- Refresh or drop stored metadata within 30 days.
- Show "YouTube" as the source and link YouTube's Terms; the privacy policy must disclose YouTube API use.
- Don't require anything beyond pressing play.
- **III.F.3.a says you may not charge users to watch embedded content.** Get a lawyer's review before putting embeds behind any paywall. For v1, keep video pages free.

**Quota:** `search.list` is capped at about 100 calls a day. `tools/youtube_mapper.py` instead lists each allowlisted channel's uploads (`playlistItems.list` + `videos.list`, 1 unit each), keeps `status.embeddable` videos, scores them against taxonomy topics offline, and writes candidates for human approval. `--refresh` re-fetches metadata older than 30 days.

### 7.2 Coverage goals (definition of "replaces books")
For every taxonomy topic (`data/taxonomy.json`), aim for:
- one reading section (original or link-out)
- one or more approved videos
- 15 or more questions (Step 1 and CK combined)
- 10 or more cards

The admin "Coverage" page shows these gaps as a heat-map by system × discipline.

Target by v1.0: **3,000 Step 1 + 3,000 Step 2 CK questions**, at least one section per topic, 2,000 or more cards. A Step 3 / CCS simulator comes in v2.

### 7.3 Authoring pipeline (`config/authoring_rules.md`)
1. AI draft (author role) from a topic plus allowed sources only (public domain / CC BY / Naadi originals).
2. Automated lint against the NBME rules (single best answer, homogeneous options, no "all of the above" or absolutes, no grammatical cues, lead-in answerable by covering the options).
3. Blind solve by the reviewer model, a different family. Disagreement means the item gets flagged.
4. Two expert reviewers in the admin UI.
5. Publish, then track psychometrics after 30 or more responses (p-value, point-biserial, distractor counts), and auto-flag outliers.

The evidence for this design (Law et al., BMC Med Educ 2025): AI items take about ¼ of the writing time but show more factual errors (6% vs 4%) and more mis-set difficulty, so expert review is mandatory. Similarity-check new items against nothing proprietary we hold; never ingest commercial Qbanks.

### 7.4 User uploads → chapters
PDF, image, video link or Anki deck goes into `uploads`. The job runs PyMuPDF text and layout extraction, falling back to the vision model for scanned pages. Content is chunked into sections with figures kept, embedded with `owner_user_id` (private), then appears in Library with the "Your file" chip. Anki `.apkg` (SQLite + media) imports into flashcards, and export writes `.apkg`.

### 7.5 Content storage and rendering: nothing hardcoded
- **The app bundle ships zero content.** No chapter text, questions, figures, PDFs, video IDs, model IDs, prices, pathway facts or copy decks go in `apps/web`. Every screen renders from the API, which reads Postgres and Storage.
- **Text and structure go in Postgres:** `chapters`, `chapter_sections.body` (TipTap JSON), `questions`, `flashcards`, `taxonomy_nodes`, `pathway_*` and `media_assets`.
- **Binary files go in Supabase Storage:**
  - `content-public`: figures, images, PDF thumbnails
  - `content-private`: PDFs, served by signed URL
  - `user-uploads`: student files
  - `vault`: personal documents
- **Video:**
  - Own large videos go in Cloudflare R2, with URLs stored in `media_assets`.
  - YouTube videos are `media_assets` rows rendered by the IFrame player.
- **Runtime configuration goes in `app_config`:**
  - model routing, budgets, search rules, feature flags, exam rules (block sizes, timers), UI copy that may change, storage bucket names
  - seeded from `config/*.yaml`, editable in an admin console (with history), cached 60 s by the API
- **Renderer:** one `<ContentRenderer>` maps TipTap JSON nodes to components (paragraph, table, callout `pearl|trap|bridge`, `figure` → `media_assets` lookup, `video`, `pdf`, `question-embed`, `mermaid`, `math`). An unknown node renders a visible error card in dev and is logged in prod; it never fails silently.
- **Importer:** `scripts/seed.py` uploads `data/figures/*.svg` to `content-public`, rewrites each `[[figure:ID]]` into a `figure` node pointing at the `media_assets` row, converts section HTML to TipTap JSON, and upserts everything. It's idempotent.
- **Admin console (`/admin`, role-gated):** create and edit chapters with the same TipTap editor; upload PDFs, figures and videos; approve YouTube candidates and AI-authored questions; edit `app_config`; view the coverage heat-map. New content goes live with no redeploy.

### 7.6 Seed import (M1)
`data/chapters.json` + `questions.json` + `figures/*.svg` + `taxonomy.json` + `question_taxonomy_map.json` + `pathway/*.json` load into the database via `apps/api/scripts/seed.py`.

Chapter section HTML contains `[[figure:ID]]` placeholders; replace them with figure blocks. Convert `body_html` to TipTap JSON (`body`) once with `@tiptap/html` `generateJSON` in a Node script, keeping the HTML as fallback.

The seed questions carry `review.humanExpertReviewed=false`: two AI review passes found no wrong keys, but a physician should spot-check before any public launch.

## 8. Screens → features → acceptance criteria

Design boards are in `design/`. "AC" = acceptance criteria for Claude Code's definition of done.

| Board(s) | Feature | AC (abridged) |
|---|---|---|
| Onboarding | Exam, date, hours, study days, materials, diagnostic | Creates profile and a generated plan; the diagnostic is a 40-question mixed block; finishes in ≤ 3 min on iPad |
| Main, PhoneToday | Today: countdown, resume, readiness, flow, by-system, cards due, pathway strip | One lime CTA; numbers come from real data; loads < 1.5 s on a warm cache |
| Courses | Course map by system with Read / Read + watch chips | Every chapter shows its coverage and progress; filter by exam/system |
| Lesson, Chapter, Tablet, PhoneChapter | Reader with video, figures, page-notes panel (type / write / both), assistant panel or sheet | Text-first; video optional; selection toolbar (Highlight / Note / Card / Ask); ink works on every page; reading position syncs across devices |
| Ink, Notebook | Ink layer; notebooks with paper templates | §5.3 budget met; Cornell/grid/dotted/ruled/blank; handwriting is searchable |
| Builder, Qbank, Exam, Results, PhoneQbank | Block builder; tutor/timed/exam modes; navigator; flag; lab-values link; results analytics by system/discipline/competency | Exam mode hides explanations and silences the assistant; timer is accurate to ±1 s across backgrounding; results show % correct, time per question, weak areas (amber) and the official weights overlay |
| Cards | FSRS review with flip and Again/Hard/Good/Easy | Uses ts-fsrs 6 defaults with optimizer-ready logs; keyboard 1–4; Anki import/export round-trips |
| Planner, PhonePlan | Exam-date-driven plan; week calendar; rebalance after missed days; reminders; calendar sync | Generator reproduces `data/study_plan_step1_60d.csv` for her inputs; rebalance moves missed items forward without breaking assessment days; ICS feed URL; Web Push for home-screen PWA (iOS 16.4+) |
| Notes | Notes, three-colour highlights, AI summary, generated cards, PDF export | Filter by colour/anchor; summary cites anchors; PDF export includes ink |
| Search | Global search: chapters, notes, handwriting, questions, uploads, web | Hybrid ranking; results < 300 ms for local content; web results labelled as from Tavily |
| Library | Uploads (PDF, image, video link, Anki) to chapters | Upload 50 MB PDF → readable chapter in < 2 min; private by RLS |
| Pathway | MBBS → Match → landing → practice: stages, tasks, document vault, cost tracker | Seeded from `data/pathway/`; each task shows `last_verified` and an official link; tasks older than 90 days get a "re-verify" badge; vault files private; costs total by stage |
| Settings | Pencil and ink, plan, appearance, offline, assistant, export | Pen-only drawing toggle; offline download size shown; export all data as JSON + PDFs |
| Phone, Dock, Nav, System | Assistant sheet, shared navigation, tokens | Phone assistant opens full-screen; tab bar centre button |

## 9. Secrets and configuration

The owner has stored secrets on Hugging Face already. The code expects the **names below**; if the stored names differ, map them in `naadi_api/settings.py` (pydantic-settings, with aliases). Never print secrets in logs.

| Env var | Kind | Used by |
|---|---|---|
| `OPENROUTER_API_KEY` | Space **secret** | LLM, embeddings, STT |
| `TAVILY_API_KEY` | Space **secret** | web_search / web_extract |
| `DATABASE_URL` | Space **secret** | Postgres (Supabase pooled URL, `sslmode=require`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Space **secret** | Server-side writes to shared content, storage signing |
| `SUPABASE_JWT_SECRET` (or JWKS URL) | Space **secret** | Verify user JWTs in FastAPI |
| `YOUTUBE_API_KEY` | Space **secret** | youtube_mapper refresh job |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Space **secrets** | Large uploads (presigned URLs) |
| `OPENROUTER_DAILY_CAP` | variable | 50 before the one-time $10 credit purchase, 1000 after |
| `HF_TOKEN` | Space **secret** | Only if using a private HF bucket |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Space **variables** (public, build-time `ARG`) | Web client auth |
| `OPENROUTER_APP_URL`, `OPENROUTER_APP_TITLE` | variables | `HTTP-Referer` / `X-Title` headers for OpenRouter attribution |
| `ADMIN_EMAILS` | variable | Content-review admin role |

## 10. Deploy (follows the huggingface-spaces skill)

**Free path (default):**
1. `pip install -U huggingface_hub`, then `hf auth whoami`. A free account needs a verified email and an account older than 30 days for ZeroGPU Spaces (max 2).
2. Create the Space: `hf repos create <ns>/naadi --type space --space-sdk gradio --flavor zero-a10g --private`. Then run `hf spaces variables add <ns>/naadi --env GRADIO_SSR_MODE=false`. Your stored secrets are already there; add any missing with `hf spaces secrets set <ns>/naadi KEY=…`.
3. CI builds `apps/web` (Vite) into `static/` and copies `deploy/free-space/{README.md,app.py,requirements.txt}` plus `apps/api/naadi_api/` to the Space root. Then it runs `hf upload <ns>/naadi ./space_build --repo-type space --exclude "**/__pycache__/**"`.
4. Verify per the skill:
   - `hf spaces info <ns>/naadi --expand runtime`
   - `hf spaces logs <ns>/naadi --tail 200`
   - `curl https://<ns>-naadi.hf.space/api/health`
   - an **SSE smoke test** on `/api/chat`; if buffered, switch to the Gradio streaming API
   - Playwright (WebKit) against the live URL

**If the account is PRO:** use `deploy/space/` instead (`--space-sdk docker --flavor cpu-upgrade` for always-on). Same app code; Python 3.13.

**Database and storage:**
- Create a **Supabase Free** project in Mumbai (ap-south-1). Enable `vector` and `pg_trgm`, then run Alembic migration 0001 (`db/schema.sql`) and `scripts/seed.py`.
- Enable email magic link and Sign in with Apple.
- Create a **Cloudflare R2** bucket for uploads over 10 MB, accessed with presigned URLs from the API.

**CI (GitHub Actions, free):**
- On push: lint/test → `tools/check_models.py` → build → upload to the Space.
- Cron daily at 03:00 UTC: `curl /api/health` (Supabase keep-alive and Space warm-up), then `pg_dump | gzip`, pushed to a private HF dataset repo (30-day retention).
- Cron weekly: `tools/youtube_mapper.py --refresh` (30-day metadata policy).

## 11. Milestones for Claude Code

| M | Scope | Done when |
|---|---|---|
| M0 | Monorepo scaffold (`apps/web`, `apps/api`, `packages/ui`, `packages/shared` types via zod ↔ pydantic), tokens, CI, single-Space deploy with `/api/health` | Live URL shows the token sheet; CI green |
| M1 | Schema + Alembic, auth, seed import, Courses, Reader (text + figures), Search (local) | All 14 chapters and 685 questions browsable on iPad; RLS tests pass |
| M2 | Qbank: builder, tutor/timed/exam, results, attempts; Cards (FSRS) | Mock A runs as a timed 2×40 exam on iPad with accurate results |
| M3 | Ink layer on sections/questions/cards + notebooks; latency benchmark; notes and highlights | Pencil latency benchmark recorded; decision on Capacitor |
| M4 | Tutor agent: LangGraph graph, tools, SSE, page context, uploads (vision), Tavily, confirm-interrupts | 30-prompt eval ≥ rubric bar; exam-mode silence verified |
| M5 | Planner + rebalance + ICS + Web Push; Pathway module with vault and costs; offline week download | Airplane-mode study day works end to end on iPad |
| M6 | Content pipeline: YouTube mapper + approvals, authoring pipeline + reviewer UI, coverage heat-map, Anki import/export | 200 new reviewed questions published through the pipeline |

## 12. Quality bar
- **Tests:** vitest (units), pytest (API, graph tools with mocked LLM), Playwright on WebKit + Chromium at iPad/phone/laptop viewports, axe-core with no WCAG 2.2 AA violations, and a real-iPad smoke checklist each milestone (Pencil, Split View, offline, install).
- **Medical content:** every published question passes the authoring pipeline. Track error reports from a "Report an issue" button on every question and section.
- **Privacy:** student uploads are private (RLS + signed URLs); ZDR model routing; no analytics on note or ink content.

## 13. Risks and open items
1. **Model IDs and prices:** verify at build time (§6.1). Several were read from OpenRouter's catalog and are newer than our tools' knowledge.
2. **YouTube paywall clause (III.F.3.a):** needs a lawyer before monetising video pages.
3. **Free hosting quirks:** the ZeroGPU Gradio Space is used as a CPU server. Confirm SSE streaming through `gradio.Server` on the first deploy. Free Spaces sleep when idle (cold start); the daily ping helps. Docker needs PRO.
9. **Free-model limits and logging:** 50 requests/day until the one-time $10 credit purchase (then 1,000/day), 20 rpm, and NVIDIA free endpoints log prompts. Strip PII and keep vault documents away from LLMs. Free model IDs change, so `check_models.py` runs in CI.
10. **Supabase Free:** pauses after 7 days idle (cron ping), 500 MB database (halfvec embeddings, pruning), no backups (nightly `pg_dump` to a private HF dataset).
11. **Tavily free:** 1,000 credits/month. Search only when course retrieval scores low, cache for 7 days, and stop at 950 credits.
4. **Web ink latency on iPad:** M3 benchmark decides on Capacitor.
5. **Content licensing:** OpenStax switched to NC-SA with no AI ingestion; StatPearls is NC-ND. Keep them link-only.
6. **Seed content review:** 685 seed questions are AI-authored and AI-reviewed. A physician spot-check is required before public launch.
7. **Pathway facts change fast:** visa and fee rules move monthly in 2026. Re-verify `pathway_tasks` quarterly and on policy news; show `last_verified` everywhere.
8. **Step 3 CCS:** format details unconfirmed; deferred to v2.
