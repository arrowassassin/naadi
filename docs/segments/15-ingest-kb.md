# Segment 15 — M4: Universal `ingest` graph, Library, knowledge base, PDFs in the canvas, `tools/model_eval`

**Implementer:** `implementer-opus` (PLAN: untrusted file handling + vision; the sensitivity gate is the vault's last line of defence). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** E (with S14, S15b). Contracts on the ledger: with `seg14` the canvas registry (`pdf` kind) and the tool registry (`add_to_knowledge`, `get_page` for `pdf_page`); with `seg15b` the Library "Generated" filter. Post the **placement handler registry** (`naadi_api/ingest/placements/`, `register_placement(kind, handler, requires_flag)`) so S16 (planner items), S17 (cost entries) and S20 (Anki) add theirs later. Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg15`).
**Depends on:** S02 (`uploads`, `kb_sources`, `ingest_items`, `vault_documents`, buckets `user-uploads`/`vault`, embeddings `ref_type` `upload`/`kb_doc`/`url`), S04 (Aegra background runs + interrupts, `llm.py` vision/structured/long_context roles, `llm_privacy.assert_no_vault`, `search.py` Tavily extract), S06 (`ContentRenderer`, figure nodes), S07 (embedding jobs, `search_local`), S10 (`POST /api/cards`), S12b (`pdf_page` ink surface `{upload_id}:{page}`), S13 (notebook pages for handwriting), S14 (canvas, tutor tools).
**HANDOFF refs:** §7.4 (uploads → chapters), §7.1 (license gates), §6.1 (PDF engine rule, vision role, privacy), §2.1 (500 MB DB, 1 GB Storage), §8 rows Library and Search. `docs/AGENTS.md` → Universal ingest (inputs, 7-step pipeline, placement table, storage budget), Knowledge base. Boards: `design/Library.dc.html`, `design/Phone.dc.html` (chat about an uploaded PDF), `design/Lesson.dc.html` (canvas panel), `design/Search.dc.html` (uploads group).

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `media_assets` gains `owner_user_id uuid` (null = shared). RLS: shared rows as today; owned rows visible only to the owner, never `approved` for publishing. Figures extracted from a user's file are owned rows.
- `sources` gets a seeded row `user-provided` (licence "user's own material", `allow_ai_ingest` true for its owner only, never publishable); every private chapter, figure and KB entry references it (or the URL's own licence for public-domain pages).
- `questions` gains `owner_user_id uuid` (null = shared): owned questions are visible only to their owner, never `published` to others, and keep the same hidden answer columns and attempt gate as shared ones.
- `ingest_items` gains `sha256`, `bytes`, `pages`, `progress smallint`, `run_id`, `placements jsonb[]` (what was created, for undo). Client: select + update of `confirmed_placement` only; everything else is the API's.
- `app_config.ingest`: `max_file_mb: 50`, `zip_max_entries: 200`, `zip_max_uncompressed_mb: 300`, `zip_max_depth: 2`, `max_vision_pages: 40`, `user_storage_mb: 600`, `audio_keep: false`; flag `library`.
### Pipeline (`naadi_api/ingest/`, graph `ingest` in `aegra.json`, one background run per item, batches grouped by `batch_id`)
1. **Receive & secure**: upload via a signed upload URL to `user-uploads/{user_id}/{item_id}`; the run sniffs type from bytes (`filetype`, pin), enforces caps, rejects executables, macro-enabled Office, encrypted PDFs/ZIPs, zip-bombs (ratio > 100, entry caps) and image-bombs (Pillow `MAX_IMAGE_PIXELS` 50 MP); SHA-256 exact duplicate → link to the existing item.
2. **Sensitivity gate (local, before any model call)**: rules + local OCR (`rapidocr-onnxruntime`, pin) for passport MRZ, Aadhaar (Verhoeff), PAN, US SSN/visa numbers, diploma/visa/receipt keywords with a name. Hit → interrupt "Store in your Vault?"; yes → object moved to `vault`, `vault_documents` row, status `vaulted`, run ends; no → continues with a second rules pass on the extracted text. Vault items never reach `llm.py` (`assert_no_vault` on every call in this graph).
3. **Extract**: PyMuPDF (text, layout blocks, embedded figures, page count; scanned pages = < 50 chars of text) and `markitdown` (pin) for DOCX/PPTX/XLSX/CSV/EPUB/HTML/RTF; images → WebP, HEIC via `pillow-heif` (pin); audio → `faster-whisper` 1.2.1 base.en int8 (audio deleted after unless `audio_keep`); URLs → `web_extract` (cached); own video files → thumbnail + metadata only; YouTube links → oEmbed only. Scanned pages → vision role (rasterized server-side, ≤ `max_vision_pages`, the rest local OCR). PDFs are never sent to an LLM as files.
4. **Understand**: one `structured`-role call (purpose `job`, PII scrubbed, ≤ 4k chars of headings + samples) → pydantic `{kind, title, taxonomy_ids, language, quality}`.
5. **Propose placement** via interrupt (confirm card in Library and, when started from chat, in the assistant): long reading → private chapter (`chapters.kind 'user_upload'`/`'user_kb'`, sections by headings or page ranges, figures as owned `media_assets`); diagram/table → figure attached as a private overlay on the matching section; handwritten/lecture notes → notebook page (image + recognized text) linked to the section; Q&A CSV/TSV → flashcards in a named deck; own MCQs → private questions (`owner_user_id`, never shared; answers still hidden until attempted); article → KB entry. Batches get one grouped proposal. Alternatives listed; only registered placements show.
6. **Index**: chunks embedded locally (owner set); near-duplicate (≥ 80 % chunks at cosine ≥ 0.97) linked, not re-added.
7. **Report**: statuses `received → extracting → needs_confirmation → placing → ready | failed (reason, retry) | vaulted`; every placement reversible ("Undo placement" deletes what `placements` lists).
### API
- `POST /api/ingest/uploads` (returns the signed upload URL + item id), `POST /api/ingest/items/{id}/start`, `POST /api/ingest/urls`, `POST /api/ingest/paste`, `GET /api/ingest/items?status=`, `POST /api/ingest/items/{id}/confirm {placement}` (resumes the interrupt), `POST .../undo`, `POST .../retry`, `DELETE`. `GET /api/library` (files, KB entries, private chapters, storage used / remaining).
- Tutor tools registered here: `add_to_knowledge(url | upload_id)` (write, interrupt) and `get_page` for `pdf_page` (page text + citation `{upload_id, page}`).
### Web
- **Library** (`/library`, linked from Learn and Courses' "Your files" group; Learn nav, no new top-level item): drop zone (PDF, link, camera via `capture`, paste; Anki shows only once S20 registers it), type filters, file cards with status words + icons, confirm cards, storage meter. Private chapters show the "Your file" chip in Library, Courses and Search.
- **PDF canvas kind**: pdfjs-dist 6.5.438 (self-hosted worker, CSP `worker-src 'self'`), signed URL, page thumbnails, ink per page through `useInkLayer('pdf_page', '{upload_id}:{page}')`, lasso → Ask (crop PNG + page text, cites the page), assistant page citations open the canvas at that page.
### `tools/model_eval/`
- 30 Naadi-original tutor prompts with rubrics (grounding, citation, safety, no invented drugs/doses, refuses leaks) + 100 held-out seed questions (deterministic pick, excluded from RAG during the eval). Graded by exact match (MCQ) and the `reviewer` role (different family) for rubrics. Bar: MCQ ≥ 75 %, rubric mean ≥ 4/5, zero safety failures. Runs on `workflow_dispatch` only (job budget); results appended to `docs/MODEL_TESTS.md`.
### Guardrails (review blockers)
- No placeholders, fake data or "coming soon"; every UI string a `ui_copy` key (`ingest.*`, `library.*`); pydantic → OpenAPI → zod; never log file bodies or extracted text.
- No answer of a private or shared question reaches the client before an attempt. Only free chains via `llm.py`, PII scrubbed before every LLM and web call. Every asset references a `sources` row; nothing the student uploads becomes shared content. No commercial bank or textbook enters shared RAG.
- Lime = one next action per view, violet = assistant proposals, paper = the student's files; status never colour-only.

## Tests
- Unit: type sniffing vs extension lies, every rejection rule, zip/image bombs, sensitivity rules on synthetic IDs (no real documents in fixtures), extractor per type (small generated fixtures), placement grouping, undo, near-dup.
- db: RLS on owned `media_assets`, private chapters and KB rows (user B sees nothing); vault rows never in embeddings; client cannot write status columns.
- Integration (CI): a generated 50 MB PDF becomes a readable, searchable private chapter in < 2 min on a `cpu-basic`-sized runner; a spy proves no vault byte and no PII reaches `llm.py`.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): upload → confirm → chapter in Library/Courses/Search; personal document → Vault prompt; PDF in the canvas with ink on page 2 → reload persists; lasso → Ask.
## Acceptance
1. 50 MB PDF → readable private chapter < 2 min via the background run; RLS-private; searchable.
2. Vault content never reaches any model; every placement waits for confirmation and can be undone.
3. 30-prompt eval meets the bar; matches the Library board; zero axe violations / console errors.

## Decisions (lead to check)
- **Schema contract change:** `media_assets.owner_user_id` (private figures), `questions.owner_user_id` (the student's own MCQs; S08's pool/selection must exclude other users' rows) and a `user-provided` `sources` row.
- Upload goes straight to Storage with a signed upload URL; caps 50 MB per file, 600 MB per user (1 GB bucket total).
- Placements for planner, costs and Anki are registered later by S16/S17/S20; until then those inputs are offered as KB entries only.
- Library lives under Learn (no new nav item). The eval runs manually, never on push.
