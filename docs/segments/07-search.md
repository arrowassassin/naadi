# Segment 07 — M1: Local search (embeddings + hybrid ranking) and the Search screen

**Implementer:** `implementer-sonnet` (bounded; design decided by S04/S05/S06). **Reviewer:** `reviewer-opus`.
**Wave:** C. **Depends on:** S02 (`embeddings` server-only, `halfvec(384)` + HNSW + GIN), S04 (layered search entry point, web layers), S05 (seeded content), S06 (reader deep links).
**HANDOFF refs:** §2.1 (budget: halfvec), §6.1 (fastembed bge-small 384 + MiniLM reranker), §8 Search row (hybrid ranking, < 300 ms local, web labelled Tavily). Board: `design/Search.dc.html`.

## Scope
- **Embedding job** (`naadi_api/embeddings/`): fastembed `BAAI/bge-small-en-v1.5` (384 d) running on the Space CPU; chunk sections (by heading/paragraph, ~200–400 tokens, keep section/figure ids), questions (**stem + options only** — never answers/explanations), pathway tasks, and later notes/handwriting/uploads/KB (S13/S15 call the same API). Only `allow_ai_ingest` sources. Idempotent by content hash; runs as an admin job after seed and incrementally on content change (hook from S05's importer and S19's editor). Model files cached in the image or a mounted cache (`FASTEMBED_CACHE_PATH`), no network at query time.
- **Hybrid query** `GET /api/search?q=&scope=&limit=`: pgvector cosine (HNSW) + `tsvector` full-text, fused with reciprocal-rank fusion, then MiniLM rerank of the top N; filters by type (chapters, questions, notes, handwriting, uploads, pathway) and the caller's visibility (shared + own; question results link to the question **without** answer fields; attempt-gated cards respected). Returns snippets with highlights and deep links. p95 < 300 ms on the seeded corpus on the Space CPU (measure).
- **Web** results via S04's layered search when the user asks for web/"latest" or local scores are low — labelled "Tavily"/source, cached, budget-guarded.
- **Search screen** (`/search`, ⌘K / Ctrl+K from anywhere, the nav search button): large query field, type filter chips, grouped results (chapters, handwriting/notes, questions, web), keyboard navigation, empty/loading/error/offline (offline: search the locally cached week once S18 exists; until then a clear offline state). "Naadi's short answer" panel appears only when the assistant flag is on (S14).
## Tests
- Unit: chunker, RRF fusion, filters, no answer text in question chunks.
- db: visibility (other users' notes never returned), embeddings never readable by clients (S02 rule), incremental re-embed on content change.
- Perf: p95 latency report on the seeded corpus.
- e2e: ⌘K → query → results grouped → open a chapter at the right section; all viewports, frozen-copy axe.
## Acceptance
1. Hybrid ranking over seeded content with correct visibility and no answer leaks.
2. Local p95 < 300 ms measured; web results labelled.
3. Matches the Search board; zero axe violations / console errors.
