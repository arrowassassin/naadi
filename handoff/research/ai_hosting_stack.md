# AI and Hosting Layer for "Naadi" (USMLE study web app): OpenRouter + LangGraph + Tavily + Hugging Face Spaces, as of 15 Nov 2026

Method note: package versions were pulled live from the PyPI JSON API and npm registry on 2026-11-15. OpenRouter model data was read from the public `GET https://openrouter.ai/api/v1/models` JSON (845k chars) through a fetch tool that summarizes pages. Only about the first 300k characters were read, and direct curl to openrouter.ai was blocked from the research sandbox. **Treat every model ID and price below as "observed 2026-11-15, re-verify with `curl https://openrouter.ai/api/v1/models` before hard-coding."** Some model names (for example "claude-fable-5.2", "gpt-6.1-terra", "gemini-4-argon") are newer than the researcher's training data and could not be cross-checked against vendor pages.

## Q1. OpenRouter model selection, features, and LangChain integration

### Takeaway
OpenRouter's catalog now has a 1M-context, tool-calling, structured-output model at every price tier. It also has its own embeddings endpoint (`/api/v1/embeddings`) and speech-to-text endpoint (`/api/v1/audio/transcriptions`), so the whole AI layer except web search can go through one OpenRouter key. Use the `models` fallback array plus `provider: {zdr: true, data_collection: "deny"}` for student uploads. MedQA is saturated (almost all frontier models score above 95%), so it can't tell current models apart. Pick on price, tool reliability and your own USMLE eval set.

### Cited Findings

**Model catalog snapshot (USD per 1M tokens, input/output; all listed as supporting `tools` and `structured_outputs` unless noted)** — [OpenRouter Models API](https://openrouter.ai/api/v1/models), fetched 2026-11-15

| Model ID | Created | Context | Input modalities | $ in / out | Cache read |
|---|---|---|---|---|---|
| anthropic/claude-fable-5.2 | 2026-11-09 | 1,000,000 | text, image, file | 8.00 / 40.00 | n/a |
| anthropic/claude-fable-5.1 | 2026-09-01 | 1,000,000 | text, image, file | 10.00 / 50.00 | 0.25 |
| anthropic/claude-opus-5.5 | 2026-09-22 | 1,000,000 | text, image, file | 4.00 / 20.00 | 0.20 |
| anthropic/claude-opus-5 | 2026-07-24 | 1,000,000 | text, image, file | 5.00 / 25.00 (`:batch` 2.50/12.50) | 0.50 |
| anthropic/claude-sonnet-5.5 | 2026-09-28 | 1,000,000 | text, image, file | 2.00 / 10.00 | 0.10 |
| anthropic/claude-haiku-5.5 | 2026-10-07 | 1,000,000 | text, image, file | 0.10 / 0.50 | n/a |
| openai/gpt-6-astra | 2026-09-04 | 1,050,000 | file, image, text | 10.00 / 50.00 (20/75 above 272k prompt tokens) | 1.00 |
| openai/gpt-6.1-sol (and -pro) | 2026-09-29 | 1,050,000 | file, image, text | 2.00 / 10.00 | 0.10 |
| openai/gpt-6.1-terra (and -pro) | 2026-10-26 | 1,050,000 | file, image, text | 1.00 / 5.00 (2.00/7.50 above 272k prompt tokens) | n/a |
| openai/gpt-6-luna | 2026-09-22 | 1,050,000 | file, image, text | 0.10 / 0.50 | 0.01 |
| google/gemini-4-argon | 2026-10-13 | 1,048,576 | audio, file, image, text, video | 2.00 / 10.00 | n/a |
| google/gemini-4-carbon | 2026-11-07 | 1,048,576 | audio, file, image, text, video | 2.00 / 10.00 | n/a |
| google/gemini-4-hydrogen | 2026-11-09 | 1,048,576 | audio, file, image, text, video | 1.50 / 7.50 | n/a |
| google/gemini-3.8-flash | 2026-09-02 | 1,048,576 | text, image, video, file, audio | 0.825 / 4.125 | 0.0825 |
| google/gemini-3.7-flash | 2026-08-13 | 1,048,576 | text, image, video, file, audio | 0.75 / 3.75 (`:batch` 0.375/1.875) | 0.075 |
| google/gemini-3.5-flash-lite | 2026-07-21 | 1,048,576 | text, image, video, file, audio | 0.30 / 2.50 (`:batch` 0.15/1.25) | 0.03 |
| google/gemini-omni-1.2-flash | 2026-11-05 | 131,072 | text, image, video | 1.50 / 9.00. **No tools, no structured outputs** | n/a |
| x-ai/grok-4.7 | 2026-09-21 | 500,000 | text, image, file | 2.00 / 6.00 | 0.50 |
| qwen/qwen3.8-max-0902 | 2026-09-03 | 1,000,000 | text, image, video | 2.00 / 6.00 | 0.25 |
| qwen/qwen3.8-flash | 2026-08-26 | 1,000,000 | text, image, video | 0.15 / 0.47 | 0.016 |
| deepseek/deepseek-v4.1-flash-1017 | 2026-10-17 | 1,048,576 | text, image | 0.02 / 0.108 | n/a |
| mistralai/mistral-large-4-0 | 2026-10-06 | 1,048,576 | text, image | 0.70 / 2.10 | n/a |
| meta/muse-spark-open | 2026-10-15 | 1,048,576 | text, image, video, file, audio | 1.25 / 4.25 | n/a |
| z-ai/glm-5.3-flash | 2026-08-26 | 1,048,576 | text, image, video | 0.0399 / 0.0784 | 0.03 |

- Batch variants (`:batch` suffix, about half price) exist for Anthropic, Google, x-ai and others. They suit offline bulk question authoring. — [OpenRouter Models API](https://openrouter.ai/api/v1/models)
- Audio input modality is listed for Gemini 4 (Hydrogen, Carbon, Argon), Gemini 3.x Flash and Flash-Lite, and Meta Muse models. — [OpenRouter Models API](https://openrouter.ai/api/v1/models)
- Possible extraction error: the summarizer reported z-ai/glm-5.3 at $0.084 in vs $0.45 in for its `:batch` variant. That inversion is suspicious. Don't rely on GLM prices without re-checking. — [OpenRouter Models API](https://openrouter.ai/api/v1/models)

**Embeddings (g)**
- OpenRouter now has `POST https://openrouter.ai/api/v1/embeddings` with `model` and `input` (a string or an array; image inputs also work) and optional `provider` routing. The page names these models: `openai/text-embedding-3-small`, `openai/text-embedding-3-large`, `perplexity/pplx-embed-v1-0.6b`, and the multimodal `nvidia/llama-nemotron-embed-vl-1b-v2:free`. You can list models with `GET /api/v1/embeddings/models`. — [OpenRouter Embeddings API reference](https://openrouter.ai/docs/api/reference/embeddings)
- That page gives no prices or dimensions. — [OpenRouter Embeddings API reference](https://openrouter.ai/docs/api/reference/embeddings)
- OpenRouter also has a cookbook for RAG with embeddings and rerank. — [OpenRouter llms.txt](https://openrouter.ai/docs/llms.txt) → https://openrouter.ai/docs/cookbook/evaluate-and-optimize/rag.md

**Speech-to-text (h)**
- Endpoint: `POST https://openrouter.ai/api/v1/audio/transcriptions`. It takes JSON (`input_audio.data` as base64 plus `format`) or OpenAI-compatible multipart (`file`, `model`, `language`, `temperature`, `response_format`, `timestamp_granularities`). Named models: `openai/whisper-1`, `openai/whisper-large-v3`, `openai/gpt-4o-transcribe`, `microsoft/mai-transcribe-1.5`, `microsoft/mai-transcribe-2`. To discover more, call `GET /api/v1/models?output_modalities=transcription`. — [OpenRouter STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt.md)
- Limits: multipart uploads are capped at 25 MB, and upstream providers time out after 60 s per request, so split long recordings. Supported formats include wav, mp3, flac, m4a, ogg, webm and aac. Whisper-type models are priced per second of audio, and each response's `usage.cost` shows the charge. — [OpenRouter STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt.md)
- Chat models can take audio directly through the Responses API (`/api/v1/responses`) with the `input_audio` content type. — [OpenRouter STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt.md)

**Provider routing, fallbacks and ZDR**
- `provider` object fields: `order`, `allow_fallbacks` (default true), `require_parameters` (default false), `data_collection` ("allow"/"deny"), `zdr` (true means ZDR endpoints only), `only`, `ignore`, `quantizations`, `sort` (price/throughput/latency, or an object with `by` and `partition`), `preferred_min_throughput`, `preferred_max_latency`, `max_price`. — [OpenRouter Provider Selection](https://openrouter.ai/docs/guides/routing/provider-selection.md)
- A per-request `zdr: true` is OR'ed with account-wide and guardrail ZDR settings. It can turn ZDR on but can't turn off enforcement that is already active. "Safety Retention" endpoints still qualify unless privacy settings say ZDR-only. — [OpenRouter Provider Selection](https://openrouter.ai/docs/guides/routing/provider-selection.md)
- The `models` array gives model fallbacks. By default (`sort.partition: "model"`) the primary model's endpoints are always tried first. — [OpenRouter Provider Selection](https://openrouter.ai/docs/guides/routing/provider-selection.md)
- Separate docs cover Model Fallbacks, ZDR, Data Collection, Provider Logging, Guardrails (spend and model-access controls), BYOK, PDF inputs, Structured Outputs, Tool Calling, Server Tools, Response Healing (fixes malformed JSON), the Web Search plugin, and a LangChain guide. — [OpenRouter docs index](https://openrouter.ai/docs/_llms/docs.txt) (URLs: /docs/guides/routing/model-fallbacks.md, /docs/guides/features/zdr.md, /docs/guides/overview/auth/byok.md, /docs/guides/features/plugins/response-healing.md, /docs/guides/community/langchain.md)

**Prompt caching**
- Automatic caching: OpenAI (reads at 0.25x or 0.5x; GPT-5.6+ charges 1.25x to write), Grok (0.25x), Moonshot, Groq, DeepSeek (0.1x reads), Z.AI, and Gemini 2.5+ implicit (0.25x reads, minimum 1,024 tokens for Flash and 4,096 for Pro). — [OpenRouter Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching.md)
- Explicit `cache_control` caching: Anthropic (5-min write 1.25x, 1-hour write 2x, reads 0.1x, up to 4 breakpoints), Alibaba Qwen, explicit Gemini (only the last breakpoint is used), and OpenAI GPT-5.6+ explicit (`prompt_cache_breakpoint`, 30 min or 12 h). — [OpenRouter Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching.md)
- Cache usage appears in `usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens` and in the `cache_discount` field. Sticky routing keeps the cache warm for 60 min. Set `session_id`, the `x-session-id` header or `prompt_cache_key` to control it. Sticky routing is skipped when `provider.order` is set manually. — [OpenRouter Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching.md)

**Usage accounting**
- Cookbook page: https://openrouter.ai/docs/cookbook/administration/usage-accounting.md. There is also an Activity page and a `/api/v1/generation` lookup. — [OpenRouter llms.txt](https://openrouter.ai/docs/llms.txt); [Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching.md)

**LangChain integration**
- A dedicated PyPI package exists: `langchain-openrouter` 0.2.9 (uploaded 2026-09-22, MIT). On npm, `@langchain/openrouter` 0.4.25 (2026-11-11). — [PyPI](https://pypi.org/project/langchain-openrouter/); [npm](https://www.npmjs.com/package/@langchain/openrouter)
- `langchain-openai` 1.8.0 (2026-11-13) can also be pointed at `base_url="https://openrouter.ai/api/v1"`, since OpenRouter's STT docs describe the API as OpenAI-compatible with that base URL. — [PyPI](https://pypi.org/project/langchain-openai/); [OpenRouter STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt.md)

**Medical-QA benchmark evidence**
- Vals.ai MedQA snapshot (2026-04-16): o1 96.52%, GPT 5.1 96.38%, Gemini 3.1 Pro Preview 96.37%, GPT 5 96.32%, GPT 5.4 (xhigh) 96.09%, o3 96.06%, GPT 5 Mini 96.06% ($0.25/$2), Gemini 3 Pro 96.03%, Claude Opus 4.5 (Thinking) 95.88%, Gemini 3 Flash 95.81% ($0.5/$3), Claude Opus 4.6 (Thinking) 95.41%, Qwen 3.5 Plus 95.21%. The page says the benchmark is archived because "performance on this benchmark has saturated", and almost all recent models score above 95%. — [Vals.ai MedQA](https://www.vals.ai/benchmarks/medqa)
- Secondary aggregators exist but weren't reviewed: [pricepertoken MedQA](https://pricepertoken.com/leaderboards/benchmark/medqa), [llmreference](https://www.llmreference.com/benchmark/medqa), [benchmarklist](https://benchmarklist.com/benchmarks/medqa/).

### Inferences
Recommended primary and fallback per use case. Model IDs come from the snapshot above, so re-verify them. Pass the fallback through the `models: [primary, fallback]` array, with `provider: {zdr: true, require_parameters: true}` on any request that carries student uploads.

| Use case | Primary | Fallback (different provider family) | Rationale |
|---|---|---|---|
| (a) Page-aware tutor chat, tools, streaming | anthropic/claude-sonnet-5.5 ($2/$10, cache read $0.10) | openai/gpt-6.1-sol ($2/$10) | Same price tier, both list tools and structured outputs, 1M context. Cache the page context as a system-prompt prefix to cut cost. For hard cases, escalate to claude-opus-5.5 ($4/$20). |
| (b) Vision: images, handwriting, scanned PDFs, diagrams | google/gemini-3.8-flash ($0.825/$4.125; image, file, video, audio) | anthropic/claude-sonnet-5.5 or openai/gpt-6.1-terra ($1/$5) | Gemini Flash is the cheapest family that takes file, image and audio input with tools. Use the PDF-inputs guide for scanned PDFs. |
| (c) Structured output: Mermaid, flashcards, JSON plans | openai/gpt-6.1-terra ($1/$5) with `response_format` json_schema | google/gemini-3.8-flash | Both list `structured_outputs`. Turn on the Response Healing plugin. Validate Mermaid server-side, for example by trying to parse it. |
| (d) Long-context summarization (whole PDFs, chapters) | google/gemini-3.8-flash (1,048,576 ctx) | openai/gpt-6.1-terra (1.05M; price rises above 272k tokens) | Cheapest 1M-context option with file input. Use `:batch` for non-interactive jobs. |
| (e) Bulk USMLE question authoring + independent reviewer | Author: anthropic/claude-opus-5.5 (or `:batch` of an Anthropic model) | Reviewer: google/gemini-4-argon ($2/$10) or openai/gpt-6.1-sol | Use different families so their errors are independent. Add human review by a physician, because MedQA saturation says nothing about item-writing quality. |
| (f) Router, classifier, guardrails | openai/gpt-6-luna ($0.10/$0.50) | anthropic/claude-haiku-5.5 ($0.10/$0.50) or google/gemini-3.5-flash-lite ($0.30/$2.50) | Very cheap and both list tools and structured outputs. Ultra-cheap open models (DeepSeek v4.1 flash, GLM-5.3 flash) are options, but check their ZDR availability. |
| (g) Embeddings | openai/text-embedding-3-small via OpenRouter `/api/v1/embeddings` | openai/text-embedding-3-large, or HF TEI on a Space | One key, one bill. pgvector handles either. |
| (h) Speech-to-text | openai/whisper-large-v3 via OpenRouter `/api/v1/audio/transcriptions` | openai/gpt-4o-transcribe, or direct audio into Gemini Flash | Built in, OpenAI-compatible multipart. Chunk recordings under 60 s of processing time. |

- Python: prefer `langchain-openrouter` (`ChatOpenRouter`) because it keeps OpenRouter-specific fields such as `provider` and `models`. `ChatOpenAI(base_url=..., extra_body={"provider": {...}, "models": [...]})` is the fallback. This hasn't been checked against the package's API docs.

### Gaps
- Only about 35% of the models JSON was read. Claude Opus/Sonnet newer than 5.5, GPT "mini" tiers, Kimi, and any `:free` models in the rest of the list weren't captured. Pricing for gpt-6.1-terra vs gpt-6.1-terra-pro (both reported at $1/$5) looks odd and should be re-checked.
- Embedding prices and dimensions weren't on the OpenRouter page. From prior knowledge, which wasn't verified this session: OpenAI text-embedding-3-small is about $0.02/1M with 1536 dims, and -large about $0.13/1M with 3072 dims, both supporting Matryoshka truncation. Jina and Voyage prices weren't researched.
- Whisper and STT per-minute prices via OpenRouter weren't on the STT page. Groq and HF Inference Providers Whisper prices weren't researched.
- Rate limits for OpenRouter (credit-based tiers) and the details of the BYOK fee weren't fetched (pages: /docs/guides/overview/auth/byok.md, /docs/guides/features/zdr.md).
- No MedQA or USMLE scores were found for the current (Sept–Nov 2026) model lineup. The Vals.ai snapshot only covers models up to about April 2026.

## Q2. LangGraph versions, architecture, and self-host vs Agent Server

### Takeaway
Use Python: `langgraph` 1.2.22 with `langgraph-checkpoint-postgres` 3.2.1, running inside your own FastAPI app on a Docker Space. The LangGraph Agent Server (`langgraph-api`) is Elastic-2.0 licensed, needs a license key for standalone self-hosting, and requires both Postgres and Redis. That is too heavy and too restrictive for a single HF Space.

### Cited Findings
- PyPI latest (checked 2026-11-15, upload date in UTC): `langgraph` 1.2.22 (2026-11-14), `langgraph-sdk` 0.4.14 (2026-11-06), `langgraph-checkpoint-postgres` 3.2.1 (2026-11-14), `langchain-core` 1.8.1 (2026-11-15, MIT), `langchain-openai` 1.8.0 (2026-11-13, MIT), `langchain-tavily` 0.2.18 (2026-04-16, MIT), `langchain-openrouter` 0.2.9 (2026-09-22, MIT), `langgraph-api` 0.18.0 (2026-11-12, **Elastic-2.0**), `langgraph-cli` 0.4.43 (2026-11-12), `tavily-python` 0.8.5 (2026-10-06). — [PyPI JSON API](https://pypi.org/pypi/langgraph/json) (same pattern for each package)
- npm latest: `@langchain/langgraph` 1.7.4 (2026-11-12), `@langchain/langgraph-sdk` 1.16.0 (2026-11-12), `@langchain/core` 1.4.1 (2026-11-13), `@langchain/openai` 1.8.4 (2026-11-11), `@langchain/tavily` 1.2.1 (2026-11-11), `@langchain/openrouter` 0.4.25 (2026-11-11). — [npm registry](https://registry.npmjs.org/@langchain/langgraph)
- Python `langchain-tavily` hasn't been released since 2026-04-16, while the JS `@langchain/tavily` was updated 2026-11-11. — [PyPI](https://pypi.org/project/langchain-tavily/); [npm](https://www.npmjs.com/package/@langchain/tavily)
- The standalone Agent Server needs `LANGGRAPH_CLOUD_LICENSE_KEY` ("LangSmith license key... used to authenticate ONCE at server start up") and egress to `https://beacon.langchain.com` for license checks and usage reporting (unless air-gapped). It requires PostgreSQL (`DATABASE_URI`: assistants, threads, runs, task queue) and Redis (`REDIS_URI`: pub-sub for streaming background runs). `LANGSMITH_API_KEY` is optional, for tracing only. You build the image yourself with `langgraph build`. — [LangChain docs: Deploy standalone server](https://docs.langchain.com/langsmith/deploy-standalone-server)
- Community discussion of running LangGraph Server OSS without keys, and an open-source re-implementation ("Aegra"), exist. — [LangChain forum](https://forum.langchain.com/t/best-practices-for-self-hosting-langgraph-server-oss-without-langgraph-keys/3779); [Aegra](https://awesome.ecosyste.ms/projects/github.com%2Faegra%2Faegra)

### Inferences
- **Python over JS:** the Python packages (`langgraph`, `langgraph-checkpoint-postgres`, `langchain-openrouter`) release often and are the reference implementation. The medical and PDF tooling (pypdf, OCR, pgvector clients) is stronger in Python, and FastAPI SSE is simple. The one point for JS: `@langchain/tavily` is fresher than Python `langchain-tavily` (April 2026). That's minor, since `tavily-python` 0.8.5 (Oct 2026) can be wrapped as a tool directly.
- **Architecture:** one `create_react_agent` / StateGraph tutor agent with these tools: `tavily_search` (domain-restricted), `retrieve_course_content` (pgvector), `create_flashcards` (structured output), `add_to_planner`, `save_note`, `render_diagram` (returns Mermaid). Put a cheap router node (model f) in front to classify intent and screen with guardrails. Checkpoint with `PostgresSaver`/`AsyncPostgresSaver` keyed by `thread_id` per user page. Stream through FastAPI `StreamingResponse` (SSE) using `graph.astream(..., stream_mode=["messages","updates","custom"])`: `messages` streams LLM tokens, `updates` streams node and tool steps, and `custom` lets tools emit progress such as "creating 10 flashcards". Use `interrupt()` for human-in-the-loop confirmation before write tools like `add_to_planner` or `save_note`, and resume with `Command(resume=...)`. These API names are from prior knowledge of LangGraph 1.x and weren't re-verified this session.
- **Self-host FastAPI rather than Agent Server:** the Elastic-2.0 license, the license key, and the Redis requirement all point to this, and an HF Space runs only one exposed port with no Redis.

### Gaps
- The LangGraph Python package license field was blank in PyPI metadata. It is believed MIT on GitHub but wasn't verified this session.
- Whether a free "Lite/Developer" self-hosted tier of the Agent Server still exists in 2026, and its node limits, wasn't confirmed.
- The current docs pages for `stream_mode` and `interrupt` weren't fetched.

## Q3. Tavily API, pricing, limits, domain filtering

### Takeaway
The Tavily free tier is 1,000 credits a month with no card. Search costs 1 credit (basic) or 2 (advanced), and pay-as-you-go is $0.008 per credit. Free accounts are limited to 100 search requests per minute, which is enough for an MVP.

### Cited Findings
- Plans: Researcher free 1,000 credits/mo; Project $30 for 4,000; Bootstrap $100 for 15,000; Startup $220 for 38,000; Growth $500 for 100,000; pay-as-you-go $0.008/credit; Enterprise custom. — [Tavily API credits](https://docs.tavily.com/documentation/api-credits)
- Credit costs: Search is 1 (basic) or 2 (advanced) per request. Extract is 1 (basic) or 2 (advanced) per 5 successful URLs, and failed extractions aren't charged. Map is 1 per 10 pages (2 with instructions). Crawl is the map cost plus the extract cost (10 pages: 3 credits basic, 5 advanced). Research costs 15–250 credits (pro) or 4–110 (mini). — [Tavily API credits](https://docs.tavily.com/documentation/api-credits)
- Rate limits are per organization or account and shared by all keys. Search: 100 RPM on Researcher (free), 1,000 RPM with PAYGO or Project/Bootstrap/Startup, 1,500 RPM on Growth/Scale. Crawl and Map are capped at 100 RPM and Research at 20 RPM on every plan. — [Tavily rate limits](https://docs.tavily.com/documentation/rate-limits)
- Package versions: `tavily-python` 0.8.5 (2026-10-06); `langchain-tavily` 0.2.18 (2026-04-16); `@langchain/tavily` 1.2.1 (2026-11-11). — [PyPI](https://pypi.org/project/tavily-python/); [npm](https://www.npmjs.com/package/@langchain/tavily)

### Inferences
- Use `TavilySearch(max_results=5, search_depth="basic", include_domains=["usmle.org","ncbi.nlm.nih.gov","cdc.gov","uspreventiveservicestaskforce.org","nih.gov","fda.gov","who.int"])` for guideline lookups. Fall back to an open search excluding low-quality domains through `exclude_domains`. The parameter names come from prior knowledge of `langchain-tavily` and weren't re-fetched.
- At roughly 1 credit per tutor search, the free tier covers about 1,000 searches a month. Beyond that, pay-as-you-go costs about $8 per 1,000 searches.

### Gaps
- The Tavily Search, Extract and Crawl parameter reference pages (`include_domains` limits, `topic`, `time_range`, `country`) weren't fetched.

## Q4. Hugging Face Spaces for production: frontend, backend, DB, auth

### Takeaway
Run the frontend as a free Static Space (`sdk: static` with `app_build_command: npm run build` and `app_file: dist/index.html`). Run the backend as a Docker Space (needs PRO). Put all state in external Postgres with pgvector, because Space disk is non-persistent and the legacy persistent storage add-on is gone. Free cpu-basic hardware sleeps when idle, so a production backend should run on cpu-upgrade at $0.03/hr (about $22/month).

### Cited Findings
- Docker Space: `sdk: docker`, default exposed port 7860 (change with `app_port`). Only one port is exposed externally, so put a reverse proxy in front if you need several. The container runs as UID 1000: use `useradd -m -u 1000 user` and `COPY --chown=user`. — [HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker)
- Variables are passed as build args (`ARG`) at build time and as environment variables at runtime. Build-time secrets use `RUN --mount=type=secret,id=NAME,mode=0444,required=true` and are read from `/run/secrets/NAME`. Runtime secrets are environment variables (`os.environ`). — [HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker)
- Data written to disk is lost on restart. To persist it, attach a Storage Bucket (`/data` is available only at runtime), use a dataset repo, or use an external DB or S3. — [HF Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker)
- Config keys: `app_build_command` (static Spaces; runs in a Job on each update and serves the output from `refs/convert/build`), `app_file` (for example `dist/index.html`), `app_port` (docker only, default 7860), `startup_duration_timeout` (default 30m), and `custom_headers` (only COEP, COOP and CORP are allowed). OAuth keys: `hf_oauth`, `hf_oauth_scopes` (openid and profile by default), `hf_oauth_expiration_minutes` (default 480, max 43200), `hf_oauth_authorized_org`. `suggested_storage` is **ignored because persistent storage is no longer available**. — [HF Spaces config reference](https://huggingface.co/docs/hub/spaces-config-reference)
- Plans: Static Spaces are free for everyone. Gradio and Docker Spaces need a paid plan to create (PRO for personal accounts, Team/Enterprise for orgs). Protected visibility needs PRO or Team. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Hardware: CPU Basic is 2 vCPU, 16 GB RAM, free (the default). CPU Upgrade is 8 vCPU, 32 GB RAM, $0.03/hr. The default disk is 50 GB and non-persistent. Free hardware goes to sleep after inactivity, and the docs say to upgrade to paid hardware to run indefinitely. Paused time isn't billed. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview); authoritative prices at [huggingface.co/pricing#spaces](https://huggingface.co/pricing#spaces)
- Custom domains work for public and protected Spaces, not private ones. Details: https://huggingface.co/docs/hub/spaces-custom-domain. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Static Spaces read variables client-side through `window.huggingface.variables`. Don't put secrets in a static Space. Variables are public, while secrets are private and not copied when a Space is duplicated. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Outbound networking allows only ports 80, 443 and 8080; other ports are blocked. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Built-in env vars: `SPACE_ID`, `SPACE_HOST`, and `SPACE_SIGNING_SECRET` (stable across restarts and replicas; use it to sign cookies, OAuth state or CSRF tokens). With OAuth: `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_SCOPES`, `OPENID_PROVIDER_URL`. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Storage Buckets are S3-like, mutable, non-versioned object storage on Xet with per-TB pricing and a free allowance. The page gives no consistency guarantees and no guidance on databases. — [HF Storage Buckets](https://huggingface.co/docs/hub/storage-buckets)

### Inferences
- **(i) Frontend as a Static Space (SPA, for example Vite + React PWA):** it's free, CDN-served and doesn't sleep like a container (inferred). The iPad-first PWA needs no SSR. Use Docker plus Node SSR only if SEO-critical marketing pages are needed.
- **(ii) Backend as a Docker Space:** run FastAPI + uvicorn on port 7860 as UID 1000, on cpu-upgrade so it never sleeps. The frontend calls `https://<owner>-<backend>.hf.space` directly. Add FastAPI `CORSMiddleware` allowing the frontend Space origin and any custom domain, since `custom_headers` can't set CORS headers. Expose a single SSE endpoint. Send heartbeat comments every 15 s to keep proxies from closing idle streams (precaution; HF timeouts are undocumented).
- **(iii) Database:** use external managed Postgres with pgvector, such as Supabase or Neon, for users, notes, planner, flashcards, embeddings and the LangGraph checkpointer. Spaces are stateless and buckets are object stores without documented consistency, so they're unsuitable for SQLite or Postgres data files. Use buckets only for user-uploaded PDFs and images.
- **(iv) Auth:** HF OAuth would force every student to create a Hugging Face account, which is poor UX for medical students. Use Supabase Auth (bundled with the Supabase Postgres, email/Google/Apple sign-in, row-level security) and verify Supabase JWTs in FastAPI. Clerk is a polished alternative but adds a vendor. Use `SPACE_SIGNING_SECRET` only if you roll your own sessions.

### Gaps
- No HF documentation on SSE or WebSocket support and timeouts, or on CORS behavior between Spaces, was found on the fetched pages.
- The exact idle-sleep timeout for cpu-basic, whether custom domains need PRO, Space repo size limits, and Storage Bucket per-TB prices weren't on the fetched pages. See [spaces-custom-domain](https://huggingface.co/docs/hub/spaces-custom-domain), [storage-limits](https://huggingface.co/docs/hub/storage-limits) and [hf.co/storage](https://huggingface.co/storage).
- How to mount a bucket into a Space is covered on https://huggingface.co/docs/hub/storage-buckets-access, which wasn't fetched.
- The spaces-sdks-static page wasn't fetched separately; the static facts above come from the config reference.
- Supabase and Neon free-tier limits (DB size, pausing of inactive projects, pgvector availability) and Clerk pricing weren't researched in this pass.
