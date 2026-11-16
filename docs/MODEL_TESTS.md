# Model live tests

Run from the build sandbox through LangGraph (`create_react_agent` with one tool), the official `langchain-nvidia-ai-endpoints` (`ChatNVIDIA`) and `langchain-openrouter` (`ChatOpenRouter`). Re-run before changing `config/models.yaml`; final role choices also pass `tools/model_eval`.

## 2026-11-16

**NVIDIA API catalog:** 143 chat models listed, 65 tool- or image-capable, **only 4 answer**: the rest return 410 (retired, 41), 404 (17), timeout (2) or 403 (1). The catalog list cannot be trusted: probe every model before use.

| Model                                                                                                   | Stream (first token) | Tool-calling agent | Structured JSON | Image (read "pH 7.21") |
| ------------------------------------------------------------------------------------------------------- | -------------------- | ------------------ | --------------- | ---------------------- |
| nvidia/nemotron-3.5-super-vl-120b-a12b                                                                  | 1.5 s                | correct, 1.4 s     | ok, 2.1 s       | correct, 1.1 s         |
| nvidia/nemotron-3-ultra-550b-a55b                                                                       | 3.7 s                | correct, 2.5 s     | ok, 7.7 s       | —                      |
| nvidia/nemotron-3.5-lightning-30b-a3b                                                                   | 3.8 s                | correct, 3.0 s     | ok, 40.6 s      | —                      |
| meta/muse-glimmer-30b                                                                                   | alive (probe 1.7 s)  | —                  | —               | —                      |
| moonshotai/kimi-k2.6, kimi-k2-thinking, deepseek-v4-pro/flash, z-ai/glm-5.1, qwen3.5-122b, gpt-oss-120b | 410 retired          |                    |                 |                        |
| google/gemma-4-31b-it                                                                                   | timeout              |                    |                 |                        |

**OpenRouter `:free`:**

| Model                            | Tool-calling agent                  | Structured JSON |
| -------------------------------- | ----------------------------------- | --------------- |
| particle-ai/particle-1           | correct, 1.4 s                      | ok, 2.0 s       |
| baidu/ernie-5.5-flash-preview    | correct, 3.3 s                      | ok, 2.3 s       |
| dots-studio/dots-3-note-preview  | correct, 5.7 s                      | ok, 4.9 s       |
| inclusionai/ling-3.2-flash       | correct, 4.1 s                      | ok, 21.9 s      |
| google/gemma-4-31b-it            | 429 (rate-limited upstream)         | 429             |
| thinkingmachines/inkling-2-small | 403 (not available to this account) | 403             |

Endpoint uptime (OpenRouter endpoints API, last 1 day): nemotron-3-ultra 95 %, lfm-3-1.7b 69 %, most others 99–100 %.

**Result:** Nemotron direct via NVIDIA first; newer non-NVIDIA labs (Particle AI, Baidu, dots.llm, InclusionAI) via OpenRouter as fallbacks; reviewer always from a non-NVIDIA, non-Baidu family.

## Search sources (2026-11-16, live from the sandbox)

| Source | Client | Result | Latency |
|---|---|---|---|
| PubMed | biopython `Bio.Entrez` | 694 hits for "diabetic ketoacidosis anion gap", 3 summaries | 0.64 s |
| Europe PMC | REST | 24,321 open-access full-text hits, licence per article | 0.62 s |
| PMC open-access figure | article page → `cdn.ncbi.nlm.nih.gov` blob | 200, image/webp, 87 KB (direct `europepmc.org/articles/…/bin/…` → 403) | — |
| MedlinePlus | NLM web service | 3 health topics | 0.62 s |
| openFDA | REST, no key | insulin glargine labels (18) | 0.76 s |
| ClinicalTrials.gov v2 | REST | 2 studies | 0.35 s |
| Wikimedia Commons | MediaWiki API | 3 nephron diagrams with licences (CC BY 3.0, CC BY-SA 3.0) and authors | 0.63 s |
| YouTube oEmbed | REST, keyless | title + IFrame HTML | 0.37 s |

**Rendering (Chromium):** all four images decoded (960–1600 px). YouTube embed: "Error 153 Video player configuration error" from a `file://` page with no referrer; **plays correctly** when served over HTTP with `origin` + `referrerpolicy="strict-origin-when-cross-origin"`.

Not yet tested live: SearXNG (deferred to segment 22, below), Tavily-free fallbacks such as You.com (not adopted).

**SearXNG (search layer 8) is deferred to segment 22.** The client (`naadi_api/search.py` `_searxng`, `parse_searxng`) and its routing are in place and unit-tested against SearXNG's documented `format=json` shape (`tests/agent/test_search_unit.py`: https-only results, fallback when every medical layer is empty and when Tavily's guard is exhausted, never called when a better layer answered). It is off (`search.layers.searxng.enabled: false`) because SearXNG is not installed in the Space image: it needs its own process (uWSGI/granian), a settings file with the JSON format enabled and a limiter, and its engines may throttle the Space's IP. To enable it: run SearXNG in the container on `127.0.0.1:8888` (or set `search.layers.searxng.base_url`), allow `format: json` in its `settings.yml`, then set `enabled: true` in `app_config.search` (admin editor, no redeploy) and re-run the search integration tests.

## Agent platform (segment 04, 2026-11-16, live from the sandbox through `naadi_api/llm.py`)

| Path | Result |
|---|---|
| `router` role → NVIDIA `ChatNVIDIA` (sync client in a worker thread; the aiohttp client ignores HTTPS_PROXY) | stream + invoke ok, 0.9–1.1 s, `llm_usage` rows written |
| `reviewer` role → OpenRouter `ChatOpenRouter` 0.2.9 on the `openrouter` 1.3.179 SDK (uv override: the package pins <1.0) | stream + invoke ok, 1.0–3.1 s |
| `ping` graph on Aegra 0.11.0 (streaming, custom block, interrupt → resume, background run, system cron) | all green in tests/agent (real NVIDIA call) |
| `tools/check_models.py --app-config management` | YAML PASS; live `app_config` not migrated/seeded on the real project yet |

**Search layers (live):** PubMed (Bio.Entrez) 5, Europe PMC 5 with licence per article, ClinicalTrials.gov 2–3, openFDA 0 for a disease query (404 = no match), MedlinePlus 0 for a 4-word term (2-word terms answer), Wikimedia Commons 4 reusable-licence images, YouTube oEmbed ok, Tavily `include_domains` → official pages (aamc.org, uscis.gov, travel.state.gov), second identical query served from cache.

**PMC figures:** `pmc.ncbi.nlm.nih.gov/articles/…` answers Python HTTP clients with a reCAPTCHA page (curl passes), so figures come from NCBI's **PMC Article Datasets** bucket (`pmc-oa-opendata.s3.amazonaws.com/<PMCID>.<v>/`), whose JSON record carries `license_code`, `is_retracted` and the media list; captions from the article JATS XML. CSP `img-src` allows that host.
