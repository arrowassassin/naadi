# Naadi Tech Stack (re-scoped, as of 15–16 November 2026)

**Re-scoped constraints:**
- Frontend and backend both run on Hugging Face Spaces, using the HF Space secrets the user has already set up.
- The AI agent is built on LangGraph (open source).
- LLMs come through OpenRouter; web search comes through Tavily.
- Every library is pinned to its latest stable version.
- The iPad is the primary device.

**Method:**
- Library versions were pulled live from the npm and PyPI registries at 2026-11-16 01:02 UTC.
- OpenRouter models were read from the live `openrouter.ai/api/v1/models` JSON. Only about 300k of its 846k characters were read, so the model list is partial.
- HF, Tavily, Supabase, Mux, Cloudflare, WebKit and caniuse facts come from their own docs or pages.
- Items marked "(unverified)" come from background knowledge and need checking before you commit to them.

## Recommended stack summary and Space layout

### Takeaway
Use two Docker Spaces plus an external Postgres database.

**Space 1, `naadi-web` (Docker, Node 22+):**
- Runs a Next.js 16.4 standalone SSR server on port 7860. It holds the UI, the PWA service worker and a thin "backend-for-frontend" (BFF) API.
- Visibility should be public or protected, because a custom domain needs one of those.

**Space 2, `naadi-api` (Docker, Python 3.12):**
- Runs FastAPI 0.163 with uvicorn on port 7860.
- Hosts the LangGraph 1.2 agent, ChatOpenAI pointed at OpenRouter, Tavily search, ingestion, Anki import/export, the flashcard scheduler (FSRS) and the calendar ICS feed.
- Streams responses over Server-Sent Events (SSE).

**Calls between the two:**
- The browser calls only `naadi-web` (same origin).
- `naadi-web` forwards `/api/*` and SSE server-to-server to `naadi-api`, adding `Authorization: Bearer $HF_TOKEN` if the API Space is private, plus an internal shared secret.
- This avoids cross-origin requests and cookie problems between `*.hf.space` origins.

**Data:**
- Do not keep the database inside a Space. Use external Supabase Postgres (with pgvector, Auth and Storage) or Neon.
- Use Postgres as the LangGraph checkpointer.
- Store user files in Supabase Storage, or in a *private* HF Storage Bucket mounted at `/data` on the API Space.

### Cited Findings
- **Paid plan required:** Gradio and Docker Spaces run on compute and need a paid plan (PRO for personal accounts) to create. Static Spaces are free. — [HF Spaces skill docs / hub docs](https://huggingface.co/docs/hub/spaces-sdks-docker); [HF GPU/hardware docs](https://huggingface.co/docs/hub/spaces-gpus)
- **Multiple ports:** a Docker Space exposes one external port (default 7860, changeable with `app_port`) but can open as many internal ports as it likes. To expose several apps, put a reverse proxy such as Nginx in front. — [HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker)
- **Space-to-Space calls:** a Space calls a private Space at `https://<target>.hf.space/<route>` with `Authorization: Bearer <HF token>`. — [HF forum, Jul 2026](https://discuss.huggingface.co/t/persistent-503-error-between-private-docker-spaces/177610/2)
- **Space-to-Space outage risk:** starting 8 July 2026, Space-to-Space `*.hf.space` requests returned 503 from awselb while external callers got 200. The likely cause was a platform regression. — [HF forum](https://discuss.huggingface.co/t/persistent-503-error-between-private-docker-spaces/177610/2)
- **CORS risk:** in June 2026 a Docker Space backend called from a Vercel frontend saw `Access-Control-Allow-Credentials` stripped from OPTIONS preflight responses, suspected to happen at the Spaces proxy. — [HF forum, Jun 2026](https://discuss.huggingface.co/t/hugging-face-spaces-proxy-suddenly-stripping-access-control-allow-credentials-header-on-options-preflight/177064/2)

### Inferences
- Same-origin BFF proxying sidesteps the CORS and credentials stripping. Keep a fallback mode for Space-to-Space 503 incidents: the browser calls `naadi-api` directly with a Supabase JWT as a Bearer token (no cookies), with explicit CORS that allows only the web origin.
- Alternative: merge both apps into one Docker Space, with Nginx on 7860 routing to Next.js on :3000 and FastAPI on :8000. This removes Space-to-Space hops entirely and halves compute cost. It is worth offering the user even though they asked for two Spaces.
- Monorepo (pnpm + Turborepo for JS, uv for Python):
  - `apps/web` (Next.js), with its own Dockerfile and README frontmatter
  - `apps/api` (FastAPI + LangGraph), with its own Dockerfile and README frontmatter
  - `packages/ink`
  - `packages/srs` (ts-fsrs client-side preview)
  - `packages/content` (TipTap schema)
  - `packages/ui`
  - `packages/types` (OpenAPI-generated TypeScript client)
- Each app deploys to its own Space git repo through a GitHub Action, for example with `hf upload` (unverified).

### Gaps
- No official HF documentation was found on request timeouts, maximum SSE/WebSocket duration, or Space repo size limits. The Docker docs page does not state resource limits. Test long SSE streams, and send periodic heartbeat comments every 15–20 s (unverified best practice).
- The HF PRO price was not verified (historically $9/month, unverified).

## Hugging Face Spaces specifics (SDKs, Docker, secrets, storage, hardware, sleep, domains)

### Takeaway
- **SDK:** use Docker for both Spaces. The Static SDK only fits a pure client-side SPA export, which would lose Next.js SSR and the BFF.
- **Hardware:** CPU Basic (2 vCPU, 16 GB, free) sleeps after about 48 h of inactivity. CPU Upgrade (8 vCPU, 32 GB, $0.03/h, about $22/month always-on) runs indefinitely.
- **Disk:** container disk is ephemeral. Persist files with a Storage Bucket at `/data` ($18/TB/month private), and keep the database external.
- **Custom domain:** needs PRO and a public or protected Space, with a CNAME pointing to `hf.space`.

### Cited Findings

**Docker setup** ([HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker))
- Set `sdk: docker` in the README YAML; the default port is 7860 and can be changed with `app_port`.
- The container runs as UID 1000. The docs suggest `RUN useradd -m -u 1000 user`, `USER user`, `WORKDIR $HOME/app`, and `COPY --chown=user` for every ADD/COPY, avoiding recursive chown.
- Disk contents are lost whenever the Space restarts. The `/data` volume exists only at runtime, not during the build. The suggested persistence options are a Storage Bucket, a Datasets repo, or an external DB or S3.

**Secrets and variables** ([HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker))
- At runtime, secrets and variables are environment variables.
- At build time, secrets must be mounted with `RUN --mount=type=secret,id=NAME,mode=0444,required=true` and read from `/run/secrets/NAME`. Variables at build time arrive as build args (`ARG`).
- `--secrets KEY=val` becomes an environment variable that visitors cannot see. Visibility can be public, private or protected; protected means the app is reachable but the repo files are private. — [HF Spaces skill (huggingface-skills)](https://huggingface.co/docs/hub/spaces-sdks-docker)

**Hardware and billing** ([HF hardware docs](https://huggingface.co/docs/hub/spaces-gpus))
- CPU Basic: 2 vCPU, 16 GB RAM, 50 GB disk, free.
- CPU Upgrade: 8 vCPU, 32 GB, 50 GB, $0.03/h.
- GPUs run from a T4 small at $0.40/h up to 8x A100 at $20/h. The H100 tiers were removed in December 2025.
- CPU Basic sleeps after inactivity ("currently 48 hours") and wakes when visited. Upgraded hardware runs indefinitely by default. A custom sleep time can stop it when idle, and stopped time is not billed.
- Billing is per minute while the Space is Starting or Running; builds are free. Paused Spaces are not billed. Replicas are available on paid hardware only, each billed separately.

**Storage** ([HF Spaces storage](https://huggingface.co/docs/hub/spaces-storage))
- Each Space gets a small ephemeral disk.
- Storage Buckets mount at a path you choose, read-write by default.
- Models, datasets and Spaces can also be mounted, but only read-only and only through the `huggingface_hub` API.
- Bucket pricing is $12/TB/month public and $18/TB/month private, with volume discounts above 50 TB. Egress and CDN are included up to an 8:1 ratio to storage. No free tier is listed. — [HF storage pricing](https://huggingface.co/storage)
- Public bucket files are publicly accessible forever at their resolve URL, so do not put PII in a public bucket. — [huggingface-skills buckets reference](https://huggingface.co/docs/hub/storage-buckets)
- Bucket I/O is S3-paced, so do not stream large files through it on every cold start. — [huggingface-skills buckets reference](https://huggingface.co/docs/hub/storage-buckets)

**Custom domains** ([HF custom domain](https://huggingface.co/docs/hub/spaces-custom-domain))
- Available on PRO, Team or Enterprise.
- The Space must be public or protected; private Spaces are not supported.
- Point a CNAME at `hf.space`.

### Inferences
- SQLite on the bucket-mounted `/data` is risky. It is object-store-backed (Xet) with S3-paced I/O, and SQLite's file locking on such mounts is likely unsafe (unverified). Ephemeral disk is wiped on restart. Decision: use external Postgres. If you must use SQLite, Litestream-style replication to object storage is the only safe pattern (unverified).
- The user's private data (exam documents, visa files) should not go in public buckets. Use a private bucket or Supabase Storage with signed URLs, and gate access through the API.
- Use CPU Upgrade, not GPU, for both Spaces, because all model inference is remote through OpenRouter. If cost matters, run the frontend on CPU Basic, since a 48 h sleep is tolerable for a personal app, and the API on CPU Upgrade.

### Gaps
- The ephemeral disk size and the exact Docker image size limit were not found.
- WebSocket support on Docker Spaces was not confirmed in the docs. SSE works over plain HTTP, but test it through the HF proxy.

## Backend data and auth (external DB)

### Takeaway
Use Supabase Pro ($25/month), or the Free tier while developing, for Postgres with row-level security, pgvector, Auth and Storage. `naadi-api` connects with psycopg 3.3.9 and SQLAlchemy 2.1.8. LangGraph state lives in `langgraph-checkpoint-postgres` 3.2.1.

### Cited Findings
Supabase Free vs Pro, from [Supabase pricing](https://supabase.com/pricing):

| Item | Free | Pro ($25/month) | Pro overage |
|---|---|---|---|
| Database | 500 MB | 8 GB | $0.125/GB |
| File storage | 1 GB | 100 GB | $0.0213/GB |
| Egress | 5 GB | 250 GB | $0.09/GB |
| MAU | 50,000 | 100,000 | $0.00325/MAU |

- **Compute:** Pro includes a $10 compute credit (Micro instance). — [Supabase pricing](https://supabase.com/pricing)
- **Pausing:** free projects pause after one week of inactivity. — [Supabase pricing](https://supabase.com/pricing)
- **Versions on PyPI (checked 2026-11-16):**
  - `langgraph-checkpoint-postgres` 3.2.1 (released 2026-11-14)
  - `langgraph-checkpoint-sqlite` 3.1.3 (2026-10-30)
  - psycopg 3.3.9 (2026-11-12)
  - SQLAlchemy 2.1.8 (2026-11-04)
  - Source: [PyPI](https://pypi.org/project/langgraph-checkpoint-postgres/)
- **supabase-js:** 2.119.0. — [npm](https://www.npmjs.com/package/@supabase/supabase-js)

### Inferences
- Put the Supabase service-role key only in the `naadi-api` Space secrets. The web Space gets only the anon key and URL, which can go in public variables.
- Neon plus your own auth is the alternative if you want plain Postgres; Supabase bundles more features.

### Gaps
- Neon pricing for 2026 was not fetched.
- Whether Supabase can connect to HF egress IPs via IPv4 was not verified (historically the direct connection was IPv6-only and needed the pooler, unverified).

## AI: LangGraph (Python vs JS), OpenRouter, Tavily

### Takeaway
Build the agent in **Python**: LangGraph 1.2.22, langchain-core 1.8.1, langchain-openai 1.8.0 (`ChatOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)`) and langchain-tavily 0.2.18.

Reasons for Python:
- the LangGraph Python ecosystem is the most mature (Postgres checkpointer, ingestion libraries);
- PDF, OCR and `.apkg`/sqlite tooling is better;
- FastAPI is the natural fit for the Docker API Space.

Use the JS stack (@langchain/langgraph 1.7.4) only if you collapse everything into one Node Space.

You do not need `langgraph-sdk` (Python 0.4.14 / JS 1.16.0) unless you run LangGraph's Agent Server API. Plain FastAPI with SSE is simpler.

### Cited Findings
- **Python versions (PyPI, checked 2026-11-16):** [PyPI](https://pypi.org/project/langgraph/)

| Package | Version | Released |
|---|---|---|
| langgraph | 1.2.22 | 2026-11-14 |
| langgraph-sdk | 0.4.14 | 2026-11-06 |
| langchain-core | 1.8.1 | 2026-11-15 |
| langchain-openai | 1.8.0 | 2026-11-13 |
| langchain-tavily | 0.2.18 | 2026-04-16 |
| tavily-python | 0.8.5 | 2026-10-06 |
| openai | 3.51.0 | 2026-11-13 |

- **JS versions (npm, checked 2026-11-16):** [npm](https://www.npmjs.com/package/@langchain/langgraph)

| Package | Version |
|---|---|
| @langchain/langgraph | 1.7.4 |
| @langchain/langgraph-sdk | 1.16.0 |
| @langchain/core | 1.4.1 |
| @langchain/openai | 1.8.4 |
| @langchain/tavily | 1.2.1 |
| @openrouter/ai-sdk-provider | 3.1.0 |
| @openrouter/sdk | 1.5.104 |

- **Tavily pricing:** [Tavily API credits](https://docs.tavily.com/documentation/api-credits.md)
  - 1,000 free API credits per month, no card needed.
  - Project plan: $30 for 4,000 credits. Bootstrap: $100 for 15k. Startup: $220 for 38k. Growth: $500 for 100k.
  - Pay-as-you-go: $0.008 per credit.
  - Basic search costs 1 credit and advanced search 2. Extract costs 1 credit per 5 URLs (basic) or 2 per 5 (advanced).
- **OpenRouter embeddings:** `POST /embeddings` serves models such as `openai/text-embedding-3-small`, `openai/text-embedding-3-large` and `perplexity/pplx-embed-v1-0.6b`. — [OpenRouter embeddings](https://openrouter.ai/docs/api-reference/embeddings)
- **OpenRouter audio input:** audio goes through `input_audio` with base64 data in the Responses API (wav/mp3) or Chat Completions (provider formats). Direct URLs are not supported. No `/audio/transcriptions` endpoint is documented. — [OpenRouter audio](https://openrouter.ai/docs/guides/overview/multimodal/audio)

### Inferences
- **Graph design:** a router node (cheap model) sends each request to one of these subgraphs:
  - `tutor` (tools: `search_course` via pgvector, `tavily_search`, `create_flashcards`, `add_note`, `plan_item`, `render_mermaid`)
  - `vision_reader`
  - `summarizer`
  - `qbank_author` followed by `reviewer`
- Write tools use LangGraph `interrupt()` for human confirmation before writing to the planner, notes or cards.
- **Streaming:** use `graph.astream(stream_mode=["messages","custom"])` and send it as SSE through `sse-starlette` 3.7.0. Typed custom events (`mermaid`, `card_draft`, `citation`) render generative UI blocks in React.
- **Citations:** keep the Tavily result URLs in state, and have the model reference them by index in structured output.
- **Fallbacks:** OpenRouter supports a `models` array and `provider` routing preferences (order, allow_fallbacks). Pass them through ChatOpenAI `extra_body` (unverified; the fetched page did not cover routing).
- **Data policy:** the user may upload documents containing personal data. Set OpenRouter's data-policy or zero-data-retention provider filtering (unverified).
- **Medical safety:** keep an "educational, not medical advice" banner, cite sources, and have the reviewer model check authored questions.

### Gaps
- Whether OpenRouter provider routing fields (`models`, `provider.order`, `allow_fallbacks`, `data_collection`) still work and how they are named was not verified this session. Check [openrouter.ai/docs](https://openrouter.ai/docs).
- Tavily rate limits were not found.

## Best OpenRouter models per use case (live catalog, partial read, 2026-11-15)

### Takeaway
Live catalog prices are listed below. Verify IDs at build time from `/api/v1/models`, because the catalog is large and only partly read, and model names change quickly. Put model IDs in config/env, not in code.

| Use | Primary | Fallback(s) | Cheap option |
|---|---|---|---|
| (a) Tutor chat with tools | anthropic/claude-sonnet-5.5 ($2/$10, 1M) | openai/gpt-6.1-terra ($1/$5, 1.05M); google/gemini-4-hydrogen ($1.50/$7.50) | n/a |
| (b) Vision, handwriting, PDFs | google/gemini-4-hydrogen (text, image, file, audio, video) | anthropic/claude-sonnet-5.5 (text, image, file) | qwen/qwen3.8-flash ($0.15/$0.47, image/video) |
| (c) Structured output (Mermaid, cards, JSON) | anthropic/claude-sonnet-5.5 | openai/gpt-6.1-terra | anthropic/claude-haiku-5.5 ($0.10/$0.50) |
| (d) Long-document summaries | google/gemini-4-hydrogen (1.05M context) | anthropic/claude-haiku-5.5 | deepseek/deepseek-v4.1-flash-1017 ($0.02/$0.108, 1M) |
| (e) Bulk USMLE question authoring | anthropic/claude-opus-5.5 ($4/$20) | anthropic/claude-sonnet-5.5 (cheaper bulk drafts) | n/a |
| (e) Independent reviewer (different provider) | google/gemini-4-argon ($2/$10) | openai/gpt-6-astra ($10/$50, top tier) | n/a |
| (f) Fast/cheap routing | deepseek/deepseek-v4.1-flash-1017 | openai/gpt-6-luna ($0.10/$0.50); anthropic/claude-haiku-5.5 | n/a |
| (g) Embeddings | openai/text-embedding-3-small via OpenRouter /embeddings | openai/text-embedding-3-large (higher quality) | n/a |
| (h) Speech-to-text | google/gemini-3.5-flash-lite ($0.30/$2.50, audio input) | google/gemini-3.8-flash ($0.825/$4.125) | iPadOS keyboard dictation (free) |

- For (d), Anthropic's direct pricing charges Haiku 5.5 prompts over 100k tokens at 5x, so compare that with OpenRouter's flat listing.
- For (e), use the Batch API or off-peak bulk generation.

### Cited Findings
- **Anthropic on OpenRouter:**
  - Sonnet 5.5: $2/$10, 1M context, text/image/file input, tools and structured outputs.
  - Opus 5.5: $4/$20, 1M context.
  - Haiku 5.5: $0.10/$0.50, 1M context, text/image/file input, tools and structured outputs.
  - Fable 5.2: $8/$40.
  - Source: [OpenRouter models API](https://openrouter.ai/api/v1/models)
- **OpenAI:** gpt-6-astra (flagship) is $10/$50 with 1.05M context. gpt-6.1-terra is $1/$5 with 1.05M context. gpt-6-luna is $0.10/$0.50. All take file/image/text input with tools and structured outputs. — [OpenRouter models API](https://openrouter.ai/api/v1/models)
- **Google:** [OpenRouter models API](https://openrouter.ai/api/v1/models)
  - gemini-4-argon and gemini-4-carbon: $2/$10, 1,048,576 context, text/image/file/audio/video input.
  - gemini-4-hydrogen: $1.50/$7.50.
  - gemini-3.8-flash: $0.825/$4.125.
  - gemini-3.5-flash-lite: $0.30/$2.50, with audio input.
- **Others:** [OpenRouter models API](https://openrouter.ai/api/v1/models)
  - deepseek-v4.1-flash-1017: $0.02/$0.108, 1M context, text and image, tools.
  - deepseek-v4-pro-0813: $0.111/$4.44, text only.
  - qwen3.8-max: $2/$6. qwen3.8-flash: $0.15/$0.47.
  - x-ai/grok-4.7: $2/$6, 500k context.
  - moonshotai/kimi-k3.1-pro: $2/$10.
  - mistral-large-4-0: $0.70/$2.10.
  - meta/muse-ember: $0.10/$0.50, audio input.
  - z-ai/glm-5.3: $0.084/$3.60.
- **Direct Anthropic pricing:** Haiku 5.5 prompts over 100k tokens cost $0.50/$2.50 instead of $0.10/$0.50. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Inferences
- The model assignments above are judgements based on price and capabilities. No medical-accuracy benchmark was consulted, so run a small in-house USMLE evaluation (about 100 held-out items) before fixing the choices.
- The authoring and reviewer models should come from different providers (Anthropic and Google), as the user asked.

### Gaps
- No embedding prices were found. Whether OpenRouter offers Voyage, Jina or other embeddings was not checked.
- No dedicated Whisper-class speech-to-text model was found in the parts of the catalog that were read.
- No medical QA benchmark results were collected.

## Frontend libraries: latest stable versions (npm, checked 2026-11-16 01:02 UTC)

### Takeaway
Pin these exact versions in `package.json` and refresh them right before scaffolding.

| Package | Version | Package | Version |
|---|---|---|---|
| next | 16.4.6 | @tiptap/core, @tiptap/react | 3.34.0 |
| react, react-dom | 19.3.1 | perfect-freehand | 1.2.3 |
| typescript | 7.0.2 | tldraw | 5.6.0 |
| tailwindcss | 4.3.5 | ts-fsrs | 6.0.0 |
| zod | 4.6.7 | pdfjs-dist | 6.5.438 |
| mermaid | 12.3.0 | pdf-lib | 1.17.1 |
| serwist, @serwist/next | 9.5.13 | dexie | 4.4.7 |
| idb | 8.0.4 | sql.js | 1.16.0 |
| jszip | 3.10.2 | @capacitor/core | 8.6.2 |
| @playwright/test | 1.65.1 | axe-core | 4.14.0 |
| vite | 8.3.8 | @sveltejs/kit | 3.2.0 |

Python backend (PyPI): fastapi 0.163.1 (2026-11-15), pydantic 2.14.3 (2026-11-05), uvicorn 0.54.0, sse-starlette 3.7.0, huggingface_hub 2.4.0.

### Cited Findings
- All versions above come from the live registries. — [npm registry](https://registry.npmjs.org/next/latest); [PyPI](https://pypi.org/project/fastapi/)
- **ts-fsrs:** MIT license, FSRS-6, Node ≥20. — [ts-fsrs GitHub](https://github.com/open-spaced-repetition/ts-fsrs)
- **tldraw:** displays a watermark unless you buy a paid Business License, and removing it is prohibited. — [tldraw license](https://tldraw.dev/legal/tldraw-license)

### Inferences
- TypeScript 7.0 is the native Go-based compiler. Check that Next.js 16.4 and your ESLint and TS plugins support it, or pin TypeScript 5.9.x if tooling breaks (unverified).
- perfect-freehand 1.2.3 is the last release in a long time and is stable. Using it is low risk.
- For the ink layer, prefer perfect-freehand with a custom overlay over tldraw (watermark and license cost). Use Dexie for the offline store.

### Gaps
- npm publish dates were not recorded per package; only the check timestamp is known.
- Licenses for TypeScript 7, Mermaid 12 and TipTap 3 Pro extensions were not re-verified.

## iPad as primary device (Safari on iPadOS 18/26)

### Takeaway
- **Ink in the web app:** iPadOS Safari 18.2 and later supports `getPredictedEvents` and pointer pressure and tilt. Draw only for `pointerType === "pen"` and set `touch-action: none` on the ink surface; that gives you good web ink.
- **Pencil hover:** pointer hover events are dispatched, but tilt and roll are missing during hover, and `twist` (barrel roll) reads 0 even on iPadOS 26.4.1.
- **Offline storage:** install as a Home Screen web app (same quotas as Safari, more likely to be granted persistent storage) and call `navigator.storage.persist()`.
- **Native wrapper:** do not start with Capacitor. Keep it as a phase-2 option for PencilKit latency, guaranteed file storage, or native push.

### Cited Findings
- **getPredictedEvents:** supported in Safari and iOS Safari 18.2+ (27.x listed). — [caniuse](https://caniuse.com/mdn-api_pointerevent_getpredictedevents)
- **Sampling rate:** Apple Pencil samples at 240 Hz versus 60 Hz frames, so use `getCoalescedEvents()`. Have a fallback for empty coalesced lists and for mouse and touch pressure quirks. — [DEV article](https://dev.to/sendotltd/reading-apple-pencil-pressure-in-the-browser-pointerevent-getcoalescedevents-and-the-2e23)
- **Coalesced-event bug:** iOS Safari 18.2 reported the wrong `pointerId` in coalesced events, and Flutter added a workaround. — [Flutter engine commit](https://dart.googlesource.com/external/github.com/flutter/engine/+/23447c4674b0837ada66d29099147e05a695cec4)
- **Choppy pointermove:** a March 2025 report says Pencil Pro `pointermove` produced segmented curves while `touchmove` was smooth, which suggests a lower pointer sampling rate. — [Apple forums](https://developer.apple.com/forums/thread/776468)
- **Hover and twist (WebKit bug 296943):** [WebKit bug 296943](https://bugs.webkit.org/show_bug.cgi?id=296943)
  - WebKit staff said hover events are already dispatched for Apple Pencil hover.
  - Tilt and roll are not reported during hover.
  - `twist` always reads 0 on iPadOS 26.4.1 with an iPad Pro M4 and Pencil Pro. An experimental roll-angle feature exists in bug 298276. The bug was still open as of April 2026.
- **Storage quotas:** [WebKit storage policy](https://webkit.org/?p=14403)
  - An origin can use up to 60% of disk, and Home Screen web apps get browser-level quotas.
  - Eviction can happen under storage pressure, quota overflow, or inactivity (ITP), least-recently-used first.
  - `persist()` is granted heuristically, for example for Home Screen apps.

### Inferences
- **Latency mitigation:**
  - Render with a `<canvas>` and `desynchronized: true` where supported.
  - Use predicted points for the live tail only, and drop them on the next event.
  - Store raw coalesced points.
  - Benchmark Pencil Pro pointer events against touch events, and fall back to `touchmove` with `Touch.force` and `altitudeAngle`/`azimuthAngle` if pointer sampling is choppy (unverified, but the forum report suggests it).
- **Web Push** works only for Home Screen-installed web apps on iOS/iPadOS 16.4+ (unverified this session). Send it from `naadi-api` with VAPID keys stored as HF secrets.
- **Offline video:** large 4K downloads are fragile under eviction. Offer offline video at 720p/1080p only and show `navigator.storage.estimate()`.
- **Capacitor 8.6.2** is the escape hatch. It wraps the same Next.js static or remote URL and adds a PencilKit plugin and native file storage. It is not needed for v1.

### Gaps
- No primary 2026 WebKit post on Web Push was fetched.
- Safari 26 (iPadOS 26) release-note specifics (for example `getCoalescedEvents` changes and IndexedDB fixes) were not fetched.
- No measured latency numbers were found.

## Other components retained from the original scope (video, search, content, testing)

### Takeaway
- **Video:** Cloudflare Stream ($5 per 1,000 stored minutes per month, $1 per 1,000 delivered, free encoding) or Mux (4K at $0.0096/min stored and $0.0032/min delivered, 100K free delivery minutes) for own-hosted video. Use the YouTube IFrame API for embeds.
- **Search:** Postgres full-text search plus pgvector hybrid in Supabase.
- **Content:** TipTap 3.34 JSON for chapters.
- **Flashcards:** ts-fsrs 6.0.0, with `.apkg` import/export done in Python on the API Space (sqlite3 + zipfile) rather than in the browser.
- **Handwriting:** MyScript Cloud (2,000 requests free per month, then $10 per 1,000), or Gemini/Claude vision through OpenRouter for batch convert-to-text.
- **Testing:** Playwright 1.65 including WebKit, plus axe-core 4.14 for WCAG 2.2 AA, plus real-iPad testing.

### Cited Findings
- **Cloudflare Stream pricing:** [Cloudflare Stream pricing](https://developers.cloudflare.com/stream/pricing)
- **Mux pricing:** [Mux pricing](https://www.mux.com/pricing)
- **MyScript pricing:** 2,000 requests free per month, $10 per 1k up to 500k. — [MyScript support](https://developer-support.myscript.com/support/discussions/topics/16000031825)

### Inferences
- Moving `.apkg` handling to Python works because Python's standard library includes sqlite3 and zipfile, and genanki is available.
- Do not stream video through HF Spaces: it is costly and has no adaptive streaming. Use Cloudflare or Mux.

### Gaps
- Bunny Stream pricing was not fetched.
- Whether Cloudflare Stream outputs 4K was not confirmed.

## Rough monthly cost (one user vs 1,000 users)

### Takeaway
- **One user:** about $60–90/month. This covers HF PRO, two CPU Upgrade Spaces (about $44), Supabase Free or Pro ($0–25), AI usage via OpenRouter (about $5–20) and Tavily (free tier).
- **1,000 users:** about $2,000–6,000/month, dominated by LLM tokens. Tavily is about $220–500 and video $500–1,600.

### Cited Findings
- **HF CPU Upgrade:** $0.03/h, billed per minute. — [HF hardware](https://huggingface.co/docs/hub/spaces-gpus)
- **Tavily:** the Startup plan is $220 for 38k credits. — [Tavily](https://docs.tavily.com/documentation/api-credits.md)
- **Model prices:** as listed above. — [OpenRouter](https://openrouter.ai/api/v1/models)

### Inferences
- **Hosting:** 730 h × $0.03 is about $21.90 per Space per month. At 1,000 users, add replicas, which are billed separately, to perhaps 2–4 replicas, about $45–90.
- **AI turns:** roughly $0.01–0.02 per Sonnet 5.5 tutor turn. Routing simple requests to deepseek-v4.1-flash, gpt-6-luna or haiku-5.5 cuts this by about 10x.
- **OpenRouter fees:** possible platform fee or credit surcharge, not verified.

### Gaps
- HF PRO price, OpenRouter fees and embedding prices were not verified.
