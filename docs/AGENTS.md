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

| Graph | Trigger | What it does |
|---|---|---|
| `tutor` | The assistant bar on every page (streaming) | Page-aware chat with all tools below; opens results in the canvas |
| `ingest` | Upload, or "add this URL/document to my knowledge base" (background run) | Extract (PyMuPDF, Tavily extract for URLs, vision for scans) → chunk → embed → private chapter / knowledge-base entry with source and status |
| `ink_ocr` | Ink saved (background run) | Handwriting → `ink_layers.recognized_text` via the vision role → indexed for search |
| `coach` | Nightly cron | Weak-area and pacing analysis → Today suggestions and Web Push nudges |
| `planner` | Missed days, or the student asks | Rebalance proposal → interrupt to confirm → plan updated |
| `pathway_verify` | Weekly cron | Re-checks pathway tasks against official domains; flags stale facts with sources |
| `author` | Admin, or nightly within budget | Draft → NBME lint → blind solve by a different model family → human review queue |
| `youtube_map` | Weekly cron | Allowlisted channels → embeddable videos → candidates for approval |

## Tutor tools

| Tool | Writes? | Notes |
|---|---|---|
| `search_knowledge(query, scope?)` | No | One hybrid index (pgvector + full text) over chapters, typed notes, handwriting, uploads, saved URLs, pathway, and questions already answered |
| `get_page(ref)` | No | Current section, question (after answering), video timestamp, PDF page |
| `web_search(query)` / `web_extract(url)` | No | Tavily, guarded; only when knowledge-base retrieval scores low or the student asks |
| `add_to_knowledge(url \| upload_id)` | Yes | Starts an `ingest` background run; result appears in Library with its source |
| `save_note` / `append_to_note` / `update_note` | Yes | Anchored to the page, a text range, a timestamp or a PDF region |
| `edit_chapter_section(section_id, change)` | Yes | Shows a diff; on accept writes a new `chapter_section_revisions` row (rollback in admin). Admin only for shared chapters; other users get a private overlay note |
| `make_flashcards(items)` | Yes | Preview cards before saving |
| `build_block(criteria)` | Yes | Creates a Qbank block (e.g. "20 renal questions on my weak spots") |
| `add_to_plan(item)` | Yes | Planner item |
| `get_analytics(metric, by, period)` | No | Typed queries only (never model-written SQL): accuracy by system/discipline/competency, time per question, readiness trend, card retention, streaks; returns data the canvas renders as charts |
| `render_diagram(mermaid)` | No | Validated server-side; preferred over image generation for mechanisms and anatomy |
| `generate_image(prompt)` / `generate_video(prompt, seconds≤5)` | Yes (saves media) | ZeroGPU Space (`naadi-gen`), quota-guarded, labelled AI-generated, stored in `generated_media` |
| `suggest_video(topic)` | No | Approved YouTube or own video from `media_assets` |
| `open_in_canvas(kind, ref)` | No | Documents, PDFs, diagrams, tables, charts, video, generated media |
| `remember(fact)` / `forget(key)` | Yes | Long-term memory in the Aegra store; visible and deletable in Settings |

## Canvas

Side panel on iPad landscape and laptop; full-screen sheet on iPad portrait and phone. Shows TipTap documents (assistant drafts, student edits), PDFs with the ink layer, Mermaid diagrams, tables, charts, video and generated media. Accepting an assistant draft turns it from violet (assistant) to paper (student-owned).

## Knowledge base

Every item has an owner, a source (URL, file, or Naadi content) and a status (`processing`, `ready`, `failed`). User-added URLs and documents are private, used as context for that user only, and never enter shared content or other users' retrieval. Shared content still follows the license gates in `config/sources.yaml`.
