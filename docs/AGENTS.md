# Naadi agents: capabilities, tools and guardrails

Naadi is a self-sufficient study platform: the student reads, practises, writes and plans, and a set of agents works alongside on every page. This document is the contract for what the agents can do. Runtime and libraries: `docs/PLAN.md` → "Agent platform", "Assistant UI". Models: `config/models.yaml`.

## Principles

1. **Writes need consent.** Every tool that changes data raises a LangGraph `interrupt()`; the UI shows a violet confirm chip with a preview (or a diff for chapter edits). Nothing is written until the student accepts.
2. **Grounded first, web second.** Answers come from the student's own knowledge base (chapters, notes, handwriting, uploads, saved URLs) before the web. Web results are cited and labelled "Tavily".
3. **Never leak answers.** A question's answer and explanation are invisible to every tool until the student has answered it. Exam mode silences the assistant entirely.
4. **Privacy.** PII is stripped before any model call. Vault documents never reach a model. Private knowledge (uploads, saved URLs, notes) is visible only to its owner (RLS) and never published.
5. **Free and bounded.** Every model call goes through `naadi_api/llm.py` (free-only routing, 18 rpm, daily cap with a 70 % chat reserve, ≤ 4 calls per turn). Web search through `naadi_api/search.py` (7-day cache, 950-credit monthly stop). Generation through the ZeroGPU quota (pauses when spent). When a budget is exhausted the agent says so and degrades to course retrieval; it never fails silently.
6. **Medical honesty.** Cites sources; says "I don't know" rather than inventing drugs or doses; generated images are labelled as AI illustrations, not clinical reference.

## Graphs

| Graph            | Trigger                                                                  | What it does                                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `tutor`          | The assistant bar on every page (streaming)                              | Page-aware chat with all tools below; opens results in the canvas                                                                            |
| `ingest`         | Upload, or "add this URL/document to my knowledge base" (background run) | Extract (PyMuPDF, Tavily extract for URLs, vision for scans) → chunk → embed → private chapter / knowledge-base entry with source and status |
| `ink_ocr`        | Ink saved, then idle, only for students who opted in (Settings → Pencil & ink; off by default) (sweep cron every 2 min; nightly retry of deferred) | Handwriting → `ink_layers.recognized_text` via the vision role → indexed for search                                                          |
| `coach`          | Nightly cron                                                             | Weak-area and pacing analysis → Today suggestions and Web Push nudges                                                                        |
| `planner`        | Missed days, or the student asks                                         | Rebalance proposal → interrupt to confirm → plan updated                                                                                     |
| `pathway_verify` | Weekly cron                                                              | Re-checks pathway tasks against official domains; flags stale facts with sources                                                             |
| `author`         | Admin, or nightly within budget                                          | Draft → NBME lint → blind solve by a different model family → human review queue                                                             |
| `youtube_map`    | Weekly cron                                                              | Allowlisted channels → embeddable videos → candidates for approval                                                                           |

## Tutor tools

| Tool                                                           | Writes?           | Notes                                                                                                                                                                                          |
| -------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `search_knowledge(query, scope?)`                              | No                | One hybrid index (pgvector + full text) over chapters, typed notes, handwriting, uploads, saved URLs, pathway, and questions already answered                                                  |
| `get_page(ref)`                                                | No                | Current section, question (after answering), video timestamp, PDF page                                                                                                                         |
| `web_search(query)` / `web_extract(url)`                       | No                | Tavily, guarded; only when knowledge-base retrieval scores low or the student asks                                                                                                             |
| `add_to_knowledge(url \| upload_id)`                           | Yes               | Starts an `ingest` background run; result appears in Library with its source                                                                                                                   |
| `save_note` / `append_to_note` / `update_note`                 | Yes               | Anchored to the page, a text range, a timestamp or a PDF region                                                                                                                                |
| `edit_chapter_section(section_id, change)`                     | Yes               | Shows a diff; on accept writes a new `chapter_section_revisions` row (rollback in admin). Admin only for shared chapters; other users get a private overlay note                               |
| `make_flashcards(items)`                                       | Yes               | Preview cards before saving                                                                                                                                                                    |
| `build_block(criteria)`                                        | Yes               | Creates a Qbank block (e.g. "20 renal questions on my weak spots")                                                                                                                             |
| `add_to_plan(item)`                                            | Yes               | Planner item                                                                                                                                                                                   |
| `get_analytics(metric, by, period)`                            | No                | Typed queries only (never model-written SQL): accuracy by system/discipline/competency, time per question, readiness trend, card retention, streaks; returns data the canvas renders as charts |
| `render_diagram(mermaid)`                                      | No                | Validated server-side; preferred over image generation for mechanisms and anatomy                                                                                                              |
| `generate_image(prompt)` / `generate_video(prompt, seconds≤5)` | Yes (saves media) | ZeroGPU Space (`naadi-gen`), quota-guarded, labelled AI-generated, stored in `generated_media`                                                                                                 |
| `suggest_video(topic)`                                         | No                | Approved YouTube or own video from `media_assets`                                                                                                                                              |
| `open_in_canvas(kind, ref)`                                    | No                | Documents, PDFs, diagrams, tables, charts, video, generated media                                                                                                                              |
| `remember(fact)` / `forget(key)`                               | Yes               | Long-term memory in the Aegra store; visible and deletable in Settings                                                                                                                         |

## Canvas

Side panel on iPad landscape and laptop; full-screen sheet on iPad portrait and phone. Shows TipTap documents (assistant drafts, student edits), PDFs with the ink layer, Mermaid diagrams, tables, charts, video and generated media. Accepting an assistant draft turns it from violet (assistant) to paper (student-owned).

## Universal ingest: anything in, placed in the right spot

The student can hand Naadi **anything** — drop a file, paste, share from the iPad share sheet, photograph a page, or give a URL — and the `ingest` graph works out what it is and where it belongs. Nothing is placed until the student confirms the placement.

### Inputs (detected by content, not file extension)

| Kind            | Examples                                                                              | Extraction                                                                                                                                          |
| --------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Documents       | PDF (digital or scanned), DOCX, PPTX, XLSX/CSV, TXT/MD, HTML, EPUB, RTF               | PyMuPDF for PDFs (text, layout, embedded figures, page images); `markitdown` (Microsoft, MIT) for Office/EPUB/HTML/CSV; scanned pages → vision role |
| Images          | Photos of notes or textbook pages, whiteboards, diagrams, screenshots, HEIC from iPad | Converted to WebP; local OCR first (`rapidocr`, CPU, private); vision role for layout, handwriting and diagram understanding                        |
| Handwriting     | Photo of handwritten notes, exported notebook pages                                   | Vision role → text + kept original image                                                                                                            |
| Audio           | Lecture recordings, voice memos                                                       | `faster-whisper` base.en on the Space CPU (background, local)                                                                                       |
| Video           | Own clip files, YouTube/other links                                                   | Own files: thumbnail + metadata only (no R2, Storage is 1 GB); YouTube: link + oEmbed metadata only (policy forbids downloading transcripts/audio)  |
| Decks and banks | Anki `.apkg`, CSV/TSV of Q&A, the student's own MCQ sets                              | Parsed into flashcards / private questions                                                                                                          |
| Web             | Article URLs, online PDFs                                                             | Tavily extract (cached), then as documents                                                                                                          |
| Bundles         | ZIP of any of the above                                                               | Unpacked with size, count and depth limits, each item ingested                                                                                      |
| Pasted text     | Anything copied                                                                       | As a document                                                                                                                                       |

### Pipeline (one LangGraph background run per item; batches grouped)

1. **Receive & secure** — stored privately under `user-uploads/{user_id}/…`; content-type sniffed from bytes; size caps; executables, macros and encrypted archives rejected; zip-bomb and image-bomb guards; SHA-256 for exact-duplicate detection.
2. **Sensitivity gate (local only, before any model call)** — rules + local OCR look for identity and personal documents (passport MRZ, ID numbers, diplomas, visas, bank or fee receipts with personal details). If it looks personal, stop and ask: "This looks like a personal document. Store it in your Vault? Vault files are never sent to AI." Vault items skip every later step.
3. **Extract** — per-type extractor above → normalized text, page images and figures with positions.
4. **Understand** — structured role (with PII stripped) classifies: what it is (reading material, lecture notes, figure/diagram, handwritten notes, flashcard deck, question set, schedule, fee receipt, article), which taxonomy topics (system × discipline), and its quality/language.
5. **Propose placement** — the agent picks the best destination(s) and shows a confirm card with alternatives:
   | Detected                                                                                                                                           | Placed as                                                                                                           |
   | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
   | Long reading material                                                                                                                              | New private chapter with sections and figures ("Your file" chip in Library and Courses)                             |
   | A diagram, image or table about a topic                                                                                                            | Figure attached to the matching chapter section (private overlay, or shared chapter revision if the admin approves) |
   | Handwritten / lecture notes                                                                                                                        | Notebook page (original image + recognized text), linked to the matching section                                    |
   | Q&A pairs, Anki decks                                                                                                                              | Flashcards in a named deck                                                                                          |
   | MCQs the student owns                                                                                                                              | Private question bank (never shared)                                                                                |
   | A timetable or plan                                                                                                                                | Planner items proposal                                                                                              |
   | Fee receipt (non-sensitive after the gate)                                                                                                         | Cost-tracker entry proposal on the matching pathway task                                                            |
   | Article / reference                                                                                                                                | Knowledge-base entry for context                                                                                    |
   | Batch uploads (e.g. 30 photos) get one grouped proposal ("12 pages → new chapter _Renal lecture 3_, 4 diagrams → figures in Acid–base, 14 cards"). |
6. **Index** — chunks embedded with fastembed (local), `owner_user_id` set, searchable alongside chapters, notes and handwriting; near-duplicates linked rather than re-added.
7. **Report** — Library shows status per item (`processing → needs confirmation → ready`, or `failed` with the reason and a retry); the assistant can explain or undo any placement (each placement is reversible).

### Storage budget

Supabase Storage is 1 GB: images become WebP (originals kept only when small), page images are rendered on demand rather than stored, audio is deleted after transcription unless the student keeps it, video files are not stored (metadata only). The Library shows remaining space.

## Knowledge base

Every item has an owner, a source (URL, file, or Naadi content) and a status (`processing`, `ready`, `failed`). User-added URLs and documents are private, used as context for that user only, and never enter shared content or other users' retrieval. Shared content still follows the license gates in `config/sources.yaml`.
