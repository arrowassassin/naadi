# Free AI options for every role of the USMLE study app (as of 15 Nov 2026)

Scope: iPad-first web app; Python LangGraph backend on a Hugging Face Docker Space (CPU); LLMs via OpenRouter; Tavily free tier for web search. Researched 15 Nov 2026. `curl` to openrouter.ai is blocked from this sandbox (proxy 403), so the model catalogue was read through WebFetch in 100k-character chunks of https://openrouter.ai/api/v1/models (845,688 chars, 467 models). A model that sat exactly on a chunk boundary could have been missed; re-run the check in the Space before you hard-code IDs.

## 1. OpenRouter free models and the best pick for each role

### Takeaway
On 15 Nov 2026 OpenRouter lists about 15 `:free` text/vision LLMs plus the `openrouter/free` router. The strongest models with published scores are NVIDIA Nemotron 3 Ultra (reasoning/tools) and Gemma 4 31B (a second model family, vision). For vision and long documents the pick is Nemotron 3.5 Super VL. For strict JSON the picks are Ling 3.2 Flash and ERNIE 5.5 Flash, which advertise `structured_outputs`. Every free endpoint is served by a single provider, and the NVIDIA free endpoints log usage, so do not send any personal data.

### Cited Findings
**Free LLM catalogue (from https://openrouter.ai/api/v1/models, read 15 Nov 2026).** T = tools, TC = tool_choice, RF = response_format, SO = structured_outputs, R = reasoning.

| id | ctx | input modalities | T | TC | RF | SO | R |
|---|---|---|---|---|---|---|---|
| nvidia/nemotron-3-ultra-550b-a55b:free | 1,000,000 | text | Y | Y | N | N | Y |
| nvidia/nemotron-3.5-super-vl:free | 1,000,000 | text, image, video | Y | Y | Y | Y | Y |
| nvidia/nemotron-3.5-lightning:free | 1,000,000 | text | Y | Y | N | N | Y |
| nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free | 256,000 | text, **audio**, image, video | Y | Y | N | N | Y |
| google/gemma-4-31b-it:free | 262,144 | image, text, video | Y | Y | Y | N | Y |
| google/gemma-4-26b-a4b-it:free | 262,144 | image, text, video | Y | Y | Y | N | Y |
| inclusionai/ling-3.2-flash:free | 1,048,576 | text, image | Y | Y | Y | Y | Y |
| baidu/ernie-5.5-flash-preview:free | 1,024,000 | text | Y | Y | Y | Y | Y |
| dots-studio/dots-3-note-preview:free | 512,000 | text, image | Y | Y | Y | Y | Y |
| particle-ai/particle-1:free | 1,048,576 | text, image | Y | Y | Y | Y | Y |
| thinkingmachines/inkling-2-small:free | 1,048,576 | text, image | Y | N | N | N | Y |
| liquid/lfm-3-1.7b:free | 262,144 | text | Y | Y | Y | Y | Y |
| recursive/flint-v1.0:free | 262,144 | text | Y | N | Y | N | Y |
| cohere/north-mini-code:free | 256,000 | text | Y | Y | N | N | Y |
| openrouter/free (router, priced $0, no `:free` suffix) | 200,000 | text, image | Y | Y | Y | Y | Y |
— [OpenRouter models API](https://openrouter.ai/api/v1/models)

- The catalogue lists no `file` input modality for any free LLM. Output for all of them is text.
- The web listing https://openrouter.ai/models?max_price=0 showed a different subset: Ling 3.2 Flash, ERNIE-5.5-Flash-Preview, Perplexity Decider V1.3 and Decider Flash V1.3 (free decision/classification models), and Recraft V4.1 Flash V2 (free text-to-image, "going away" 28 Nov 2026). The two Decider models did not appear in the API chunks I read. They may have sat on a chunk boundary or be a web-only listing. — [OpenRouter models, max_price=0](https://openrouter.ai/models?max_price=0)
- Ling 3.2 Flash: a hybrid reasoning model that takes text and image input, has a 1M context and accepts up to 200 images per request. Thinking can be turned off per request. — [OpenRouter free models page](https://openrouter.ai/models?max_price=0)
- ERNIE-5.5-Flash-Preview: 280B total / 14B active parameters, 1M context, covers coding, agentic tasks and deep research. The vendor claims more than 200 tokens per second. — [OpenRouter free models page](https://openrouter.ai/models?max_price=0)
- Nemotron 3 Ultra: a MoE model with 55B active / 550B total parameters on a hybrid Transformer-Mamba design, 1M context, aimed at reasoning and agent orchestration. Released 4 Jun 2026. Its free endpoint is served only by NVIDIA (P50 latency 1.56 s, 37 tok/s, 98.19% uptime). Third-party scores shown on the page: GPQA Diamond 86.7% (Artificial Analysis) and 86.1% (Vals AI); MMLU-Pro 85.8%, LiveCodeBench 86.0% and SWE-bench 69.0% (Vals AI); τ²-Bench Telecom 83.3% and IFBench 81.4% (Artificial Analysis). No MedQA score is reported. — [OpenRouter model page (secondary: AA/Vals)](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free)
- Gemma 4 31B IT: a 30.7B dense multimodal model (text and image in), 256K context, configurable thinking, native function calling, 140+ languages, Apache-2.0, released 2 Apr 2026. Its free endpoint is served only by Google AI Studio (P50 latency 14.05 s, 13 tok/s, 99.73% uptime). Artificial Analysis reports GPQA Diamond 85.7%, HLE 23.6% and IFBench 75.6%. The page does not mention PDF input. — [OpenRouter model page (secondary: AA/Vals)](https://openrouter.ai/google/gemma-4-31b-it:free)
- Nemotron 3.5 Super VL: a MoE model with 12B active / 120B total parameters, 1M context. It takes text, images, documents, GUI screenshots and video. Reasoning is on by default and can be turned off. Released 15 Oct 2026. The free endpoint is NVIDIA-only (P50 latency 10.27 s, 36 tok/s, 99.71% uptime). Artificial Analysis scores: AA-LCR (long-context reasoning) 69.0%, HLE 30.7%, Non-Hallucination Rate 69.5%. — [OpenRouter model page (secondary: AA)](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free)
- **PDFs:** the `mistral-ocr` engine costs $2 per 1,000 pages. `cloudflare-ai` is **free** and converts PDFs to markdown. `native` is charged as input tokens. `pdf-text` is deprecated and now redirects to `cloudflare-ai`. **If you don't name an engine, OpenRouter uses the model's native file support when it has one, and otherwise defaults to the paid `mistral-ocr`.** — [OpenRouter PDF docs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs)

### Inferences
- **(a) Tutor chat (tools + medical reasoning):** primary `nvidia/nemotron-3-ultra-550b-a55b:free`. It has the highest published GPQA/MMLU-Pro of the free models, plus tools, tool_choice and 1M context. Fallbacks: `google/gemma-4-31b-it:free`, then `inclusionai/ling-3.2-flash:free`. There is no free-model MedQA data, so MMLU-Pro and GPQA stand in for medical reasoning.
- **(b) Vision (images, handwriting, PDFs):** primary `nvidia/nemotron-3.5-super-vl:free` (documents, screenshots, structured outputs). Fallbacks: `google/gemma-4-31b-it:free`, then `inclusionai/ling-3.2-flash:free`. For PDFs, always pass `plugins:[{id:"file-parser",pdf:{engine:"cloudflare-ai"}}]` so OpenRouter never bills mistral-ocr. Rasterize handwritten or scanned pages in the browser and send them as images.
- **(c) Structured JSON / Mermaid:** primary `inclusionai/ling-3.2-flash:free`, which has `structured_outputs` and so supports json_schema. Fallbacks: `baidu/ernie-5.5-flash-preview:free`, then `nvidia/nemotron-3.5-super-vl:free`. Nemotron Ultra and Gemma 4 lack `structured_outputs`, so validate their output with Pydantic and retry. Mermaid is plain text, so validate it server-side or with mermaid.parse in the client.
- **(d) Long-context summarization:** primary `nvidia/nemotron-3.5-super-vl:free` (best AA-LCR evidence, 1M context). Fallbacks: `inclusionai/ling-3.2-flash:free` and `baidu/ernie-5.5-flash-preview:free` (both about 1M context).
- **(e) Question authoring:** primary `nvidia/nemotron-3-ultra-550b-a55b:free`. Fallbacks: `google/gemma-4-31b-it:free`, then `baidu/ernie-5.5-flash-preview:free`.
- **(f) Independent reviewer from a different family:** primary `google/gemma-4-31b-it:free` (Google, not NVIDIA). Fallbacks: `baidu/ernie-5.5-flash-preview:free`, then `inclusionai/ling-3.2-flash:free`. If the reviewer itself falls back to an NVIDIA model, mark that review as not independent.
- **(g) Cheap router/guard:** primary `liquid/lfm-3-1.7b:free` (1.7B, has structured outputs). Fallbacks: `nvidia/nemotron-3.5-lightning:free`, then `google/gemma-4-26b-a4b-it:free`. A cheaper option is deterministic Python rules for routing, which spend no request at all. That matters because every guard call counts against the daily cap.
- Use `openrouter/free` as the last entry in a fallback chain. It is $0 and advertises tools and structured outputs, but you don't control which model answers.
- Free endpoints are mostly single-provider (NVIDIA or Google AI Studio). The 10–14 s P50 latency on Gemma and Super VL means the UI has to stream responses.

### Gaps
- No MedQA or USMLE-style benchmark was found for any free model. The ranking rests on general reasoning benchmarks that OpenRouter cites from Artificial Analysis and Vals AI.
- No benchmark evidence was found for Ling 3.2 Flash, ERNIE 5.5 Flash, LFM-3 1.7B, dots-3-note, particle-1, inkling-2-small or flint. Their picks are based on capability flags only.
- The free LLM providers for Ling, ERNIE, Liquid and the others, and their data policies, were not checked one by one.

## 2. OpenRouter free-tier limits, data retention and fallback behaviour

### Takeaway
Free models allow 20 requests per minute. The daily cap is 50 requests if you have bought less than $10 of credits all-time, and 1,000 per day once you have bought at least $10. The FAQ describes the free cap as an account-wide total, so falling back between free models does not get around it. NVIDIA's free endpoints log prompts, so keep personal data out of free calls. `data_collection:"deny"` or `zdr:true` will probably exclude those endpoints.

### Cited Findings
- Free-model limits by all-time credits purchased: under 10 credits gives 20 rpm and 50 requests/day; at least 10 credits gives 20 rpm and 1,000 requests/day. The tier depends on all-time purchases, not on `is_free_tier`. — [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- Free-model 429s carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers. The docs say to honour `Retry-After` and "wait and retry rather than immediately re-sending". A 429 can also come from the upstream provider. `GET /api/v1/key` reports `free_model_daily_requests` (used/limit/remaining) but not the per-minute limit. A negative balance can produce 402 errors even on free models. Cloudflare DDoS protection blocks traffic that is far above reasonable use. — [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- The FAQ says free models have "low rate limits (50 requests per day total) and are usually not suitable for production use", and that buying at least 10 credits raises the limit to 1,000/day. — [OpenRouter FAQ](https://openrouter.ai/docs/faq)
- In the `models` fallback array, "If the first model returns an error, OpenRouter will automatically try the next model in the list." By default any error can trigger a fallback, including context-length errors, moderation flags, rate limiting and downtime. You are billed for the model that actually served the request, so a fallback from `:free` to a paid model is billed at the paid price. List only `:free` models if requests must stay free. — [OpenRouter model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- OpenRouter itself does zero logging of prompts and completions unless you opt in. Providers that log, or whose policy OpenRouter cannot confirm, are not routed to unless the model-training toggle in privacy settings is on. — [OpenRouter FAQ](https://openrouter.ai/docs/faq)
- "There are separate settings for paid and free models" for training. Opting out of training means OpenRouter will not route to providers that train. — [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging)
- Provider object parameters: `data_collection:"deny"` uses "only providers which do not collect user data". `zdr:true` routes only to ZDR endpoints. If no provider matches your privacy level, the request errors. — [OpenRouter provider selection](https://openrouter.ai/docs/guides/routing/provider-selection); [FAQ](https://openrouter.ai/docs/faq)
- NVIDIA free endpoints (Nemotron 3 Ultra, Nemotron 3.5 Super VL, Nemotron embed) say "please do not upload any confidential information or personal data". Usage is logged "for security purposes and to improve NVIDIA products and services" under the NVIDIA API Trial Terms. — [Nemotron 3 Ultra free](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free); [Nemotron 3.5 Super VL free](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free)
- Secondary discussion of what "free" costs on OpenRouter: [TrueFoundry blog](https://www.truefoundry.com/blog/openrouter-free-models-whats-actually-free-and-what-it-costs-you) (not read in full).

### Inferences
- With 50/day and no credits, a real tutor session would use up the quota in minutes, because each turn makes several calls (guard, tutor, tools, reviewer). A one-time $10 credit purchase is the practical minimum: it gives 1,000/day while every call still costs $0 as long as the chains contain only `:free` IDs.
- The daily cap is account-wide, so the `models` array helps with upstream 429s and outages but not with the platform's daily cap. When `free_model_daily_requests.remaining` gets low, degrade gracefully (for example, use cached answers or skip the reviewer pass).
- The NVIDIA free endpoints log usage and appear unable to meet `data_collection:"deny"` or ZDR. Setting either flag would likely leave the NVIDIA picks without an eligible endpoint, so the request would error. Remove user identifiers before calling, and keep any PII-bearing feature (for example, uploaded personal documents) off free endpoints.

### Gaps
- No primary source says which free providers pass `data_collection:"deny"`. This needs a test call per model.
- Whether the 20 rpm cap is per model or account-wide was not stated.
- Whether free embedding calls count toward the 50/1,000 daily counter was not stated.

## 3. Free embeddings

### Takeaway
OpenRouter now lists four free NVIDIA embedding models. The most robust free option is still a small model run locally on the Space CPU with fastembed (ONNX), such as BAAI/bge-small-en-v1.5 at 384 dimensions and 67 MB. It has no rate limit and sends no data to a third party. Hugging Face Inference Providers gives free accounts $0 of monthly credit.

### Cited Findings
- OpenRouter lists 39 embedding models, four of them free: `nvidia/nemotron-3.5-embed-1b:free` (32,768 ctx), `nvidia/nemotron-3.5-embed-8b:free` (32,768), `nvidia/nemotron-3-embed-1b:free` (4,096) and `nvidia/llama-nemotron-embed-vl-1b-v2:free` (131,072). The cheapest paid one is `perplexity/pplx-embed-v1-0.6b` at $0.004 per 1M tokens. Other low-cost options include `baai/bge-m3` at $0.01/M and `qwen/qwen3-embedding-8b`. — [OpenRouter embeddings models API](https://openrouter.ai/api/v1/embeddings/models)
- Nemotron 3.5 Embed 1B (free): multimodal and multilingual retrieval over text and page images, 2048-dim vectors that can be truncated, 33K context, NVIDIA-only provider. NVIDIA logs usage, and the page says not to upload personal data. Released 21 Oct 2026. No MTEB scores are listed. — [OpenRouter model page](https://openrouter.ai/nvidia/nemotron-3.5-embed-1b:free)
- fastembed supported models (dimensions / size / license): BAAI/bge-small-en-v1.5 384 / 0.067 GB / MIT; BAAI/bge-base-en-v1.5 768 / 0.21 GB / MIT; nomic-ai/nomic-embed-text-v1.5 768 / 0.52 GB / Apache-2.0 (a Q-quantized build is 0.13 GB); snowflake-arctic-embed-s 384 / 0.13 GB / Apache-2.0; snowflake-arctic-embed-m 768 / 0.43 GB / Apache-2.0; all-MiniLM-L6-v2 384 / 0.09 GB / Apache-2.0. Rerankers: Xenova/ms-marco-MiniLM-L-6-v2 at 0.08 GB and BAAI/bge-reranker-base at 1.04 GB (MIT). bge-m3 and MedCPT are not in that table. — [fastembed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- Latest versions on PyPI/npm as of 15 Nov 2026: fastembed 0.9.1 (24 Oct 2026), sentence-transformers 6.3.0 (11 Nov 2026), faster-whisper 1.2.1 (31 Oct 2025), and @huggingface/transformers (transformers.js) 4.3.2. — [PyPI fastembed](https://pypi.org/project/fastembed/); [PyPI sentence-transformers](https://pypi.org/project/sentence-transformers/); [PyPI faster-whisper](https://pypi.org/project/faster-whisper/); [npm @huggingface/transformers](https://www.npmjs.com/package/@huggingface/transformers)
- HF Inference Providers monthly credits: free users get none and must buy credits or upgrade; PRO gets $2.00/month; Team/Enterprise get $2.00 per seat, shared. — [HF Inference Providers pricing](https://huggingface.co/docs/inference-providers/pricing)

### Inferences
- **Recommendation:** primary embedding model is `BAAI/bge-small-en-v1.5` via fastembed 0.9.1, run locally on the Space CPU. It is MIT-licensed, 384-dim and 67 MB, so the index stays small. Optional rerank: `Xenova/ms-marco-MiniLM-L-6-v2`. A free cloud fallback, or a way to bulk-embed course content one time, is `nvidia/nemotron-3.5-embed-1b:free`. Only send it non-personal course text, and never mix its 2048-dim vectors into the same index as the local 384-dim ones.
- Unsourced estimate: a 384-dim small model on 2 shared vCPUs should handle tens of short chunks per second. Benchmark this on the Space before you rely on it.
- MedCPT (ncbi/MedCPT-Query-Encoder / Article-Encoder) would need sentence-transformers with PyTorch, which makes the image larger. It is only worth it if retrieval quality on medical queries turns out to be poor.

### Gaps
- No CPU throughput benchmark was found for these models on HF Space hardware.
- License and dimensions for MedCPT and bge-m3 were not verified in this pass (bge-m3 is known to be 1024-dim and multilingual, but that was not re-checked).

## 4. Free speech-to-text

### Takeaway
OpenRouter has one free model that accepts audio input, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`, but it is an LLM rather than a dedicated speech-to-text model. The most private and quota-free path is on-device: iPadOS dictation, or Whisper in Safari via transformers.js on WebGPU, which ships in iPadOS 26. faster-whisper tiny/base on the Space CPU is the server-side fallback.

### Cited Findings
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` takes text, audio, image and video, with 256K context and tools. It is the only free model with audio input in the catalogue. — [OpenRouter models API](https://openrouter.ai/api/v1/models)
- The paid audio-input models seen include Voxtral Small 24B and the Gemini 2.5 family. — [OpenRouter models API](https://openrouter.ai/api/v1/models)
- "WebGPU is available in macOS Tahoe 26, iOS 26, iPadOS 26, and visionOS 26." (web.dev, 25 Nov 2025) — [web.dev](https://web.dev/blog/webgpu-supported-major-browsers)
- transformers.js latest is 4.3.2. — [npm](https://www.npmjs.com/package/@huggingface/transformers)
- faster-whisper latest is 1.2.1. — [PyPI](https://pypi.org/project/faster-whisper/)

### Inferences
- Recommended order: (1) iPadOS system dictation into the text box, which costs nothing and needs no code. (2) transformers.js with `onnx-community/whisper-base` (or tiny.en) on WebGPU, falling back to WASM, for in-app recording. (3) faster-whisper `base.en` int8 on the Space CPU. (4) The Nemotron Omni free model only as a last resort, because it uses daily quota and NVIDIA logs the audio (voices count as personal data according to the NVIDIA page wording).

### Gaps
- I did not verify that WebGPU is enabled by default in Safari 26 on iPad (web.dev says "available"), nor Whisper speed on an iPad GPU.
- The iPadOS dictation feature was not sourced.

## 5. Tavily free plan

### Takeaway
The free plan gives 1,000 credits a month. Basic search costs 1 credit and advanced search 2. Extract costs 1 credit per 5 URLs (basic) or 2 per 5 (advanced). Free keys allow 100 requests per minute. Research tasks cost 4 to 250 credits each, so avoid them.

### Cited Findings
- The free plan includes "1,000 free API Credits every month" with no card required. Search: basic 1 credit, advanced 2. Extract: basic 1 credit per 5 successful URLs, advanced 2 per 5. Map: 1 credit per 10 pages, 2 with instructions. Crawl costs map plus extract (for example, 10 pages basic is 3 credits). Research: `mini` 4–110 credits, `pro` 15–250. Failed extract and map requests are not charged. — [Tavily API credits](https://docs.tavily.com/documentation/api-credits)
- Rate limits for development (free) keys: default API 100 rpm, crawl 100 rpm, research task creation 20 rpm, usage endpoint 10 requests per 10 minutes. Production keys get 1,000 rpm but need a paid plan or PAYGO. A 429 should be handled by honouring `Retry-After`. — [Tavily rate limits](https://docs.tavily.com/documentation/rate-limits)

### Inferences
- Budget: 1,000 credits a month is about 33 basic searches a day. Best practices:
  - Search only when retrieval over the course content falls below a score threshold.
  - Always use `search_depth="basic"` and `max_results` of 5 or fewer.
  - Set `include_answer=false` and use the returned snippets rather than calling extract.
  - Use `include_domains` (for example ncbi.nlm.nih.gov, cdc.gov, who.int, fda.gov, nice.org.uk, usmle.org) to get better hits per credit.
  - Cache normalized queries for at least 7 days in the Space's persistent storage or a database.
  - De-duplicate queries per session and enforce a per-user daily quota.
  - Never call `/research` or `/crawl`.
  - Check the usage endpoint at most every 10 minutes.

### Gaps
- The doc does not say whether failed search requests are charged, or whether the "fast"/"ultra-fast" search depths are available and what they cost.

---

## Final recommended `roles:` mapping

```yaml
openrouter:
  require_credits_purchase_usd: 10   # one-time; raises :free cap 50/day -> 1000/day (20 rpm unchanged)
  only_free_ids_in_chains: true      # a paid fallback is billed at paid price
  pdf_engine: cloudflare-ai          # free; default would be mistral-ocr ($2/1k pages)
  privacy: strip PII; NVIDIA free endpoints log usage; don't set data_collection:deny/zdr on free chains (likely no eligible provider)

roles:
  tutor:           { primary: nvidia/nemotron-3-ultra-550b-a55b:free, fallbacks: [google/gemma-4-31b-it:free, inclusionai/ling-3.2-flash:free, openrouter/free] }
  vision:          { primary: nvidia/nemotron-3.5-super-vl:free,      fallbacks: [google/gemma-4-31b-it:free, inclusionai/ling-3.2-flash:free] }
  structured:      { primary: inclusionai/ling-3.2-flash:free,        fallbacks: [baidu/ernie-5.5-flash-preview:free, nvidia/nemotron-3.5-super-vl:free] }
  summarizer:      { primary: nvidia/nemotron-3.5-super-vl:free,      fallbacks: [inclusionai/ling-3.2-flash:free, baidu/ernie-5.5-flash-preview:free] }
  question_author: { primary: nvidia/nemotron-3-ultra-550b-a55b:free, fallbacks: [google/gemma-4-31b-it:free, baidu/ernie-5.5-flash-preview:free] }
  reviewer:        { primary: google/gemma-4-31b-it:free,             fallbacks: [baidu/ernie-5.5-flash-preview:free, inclusionai/ling-3.2-flash:free] }  # must differ in family from author
  router_guard:    { primary: liquid/lfm-3-1.7b:free,                 fallbacks: [nvidia/nemotron-3.5-lightning:free, google/gemma-4-26b-a4b-it:free] }  # prefer rule-based routing first
  embeddings:      { primary: local fastembed==0.9.1 BAAI/bge-small-en-v1.5 (384d, 67MB, MIT), rerank: Xenova/ms-marco-MiniLM-L-6-v2, cloud_fallback: nvidia/nemotron-3.5-embed-1b:free (2048d, non-PII only) }
  stt:             { primary: iPadOS dictation, secondary: transformers.js@4.3.2 whisper-base WebGPU->WASM, server: faster-whisper==1.2.1 base.en int8, last_resort: nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free }
  web_search:      { provider: tavily free (1000 credits/mo, 100 rpm), depth: basic, max_results: 5, include_domains: [ncbi.nlm.nih.gov, cdc.gov, who.int, fda.gov, usmle.org], cache_ttl_days: 7, never: [research, crawl] }
```
