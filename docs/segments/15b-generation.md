# Segment 15b — M4: Image and video generation on the `naadi-gen` ZeroGPU Space

**Implementer:** `implementer-sonnet` (bounded service per this brief). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** E (with S14, S15). Contracts on the ledger: with `seg14` the tool registry (`generate_image`, `generate_video`) and canvas kind `generated_media`; with `seg15` the Library "Generated" filter. Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg15b`).
**Depends on:** S02 (`generated_media`, bucket `generated-media`), S03 (admin gate), S04 (Aegra custom stream events, `llm_privacy.scrub_generic`, `/api/health` panel), S06 (figure node, `media_assets`), S14 (tool registry, interrupts, canvas, answer blocks `image`/`video`).
**HANDOFF refs:** §4 (violet = assistant output), §7.1 (every asset has a `sources` row), §12 privacy. `config/models.yaml` → `generation` (space id, FLUX.1-schnell, LTX-Video ≤ 5 s, per-user caps, label, `publish_requires_admin_approval`). `docs/AGENTS.md` principles 5–6, tools table. PLAN decision "Image/video generation". Boards: `design/Lesson.dc.html` (assistant panel blocks), `design/Phone.dc.html`, `design/Library.dc.html` (filters), `design/System.dc.html` (chips).

## Scope
### `naadi-gen` Space (`deploy/gen-space/`: `README.md` frontmatter `sdk: gradio`, ZeroGPU, private; `app.py`; `requirements.txt` without `gradio`/`spaces`/`huggingface_hub`)
- Two `gr.api` endpoints, no UI: `/image {prompt, width ≤ 1024, height ≤ 1024, seed}` → WebP (FLUX.1-schnell, 4 steps, bf16) and `/video {prompt, seconds ≤ 5, seed}` → MP4 H.264, ≤ 768×512, 24 fps, ≤ 8 MB (LTX-Video). `@spaces.GPU(duration=…)` with a dynamic duration callable; models load at start on CPU and move to CUDA inside the GPU function (ZeroGPU rules). Before building, confirm the LTX-Video licence allows this use and the model fits ZeroGPU large; if not, video ships disabled (flag off, no UI) and the decision is posted.
- CI workflow `deploy-gen.yml` (path filter `deploy/gen-space/**`, `workflow_dispatch`) uploads with `hf upload … --repo-type space`; never requests paid hardware.
### API (`naadi_api/generation/`)
- `gradio_client` (pin exact) with `HF_GEN_TOKEN` read through `settings.py` (fine-grained token, access to that Space only; listed in `docs/SECRETS.md` and `check_env.py`). Missing token → generation reports `not_configured` in `/api/health` and the tools and UI stay hidden.
- Quota guard: per-user daily caps from `generation.image/video.max_per_user_per_day` (counted from `generated_media`), plus ZeroGPU's own quota: its "quota exceeded" error → `paused` state with the reset time (parsed from the error, else next 00:00 UTC); never retried in a loop, never routed to paid inference.
- Prompt handling: `scrub_generic` before sending; refuse prompts naming real people or asking for patient photos or graphic gore (rules, no LLM); the stored prompt is the scrubbed one.
- Storage: result → `generated-media/{user_id}/{id}.webp|.mp4`; `generated_media` row (`model`, size/duration, `ai_generated` true); signed URLs (10 min).
- Tutor tools (write, interrupt first: preview shows the prompt, kind and remaining quota): `generate_image(prompt)`, `generate_video(prompt, seconds ≤ 5)`. Progress streams as custom `progress` events (queued → generating → saving) from `gradio_client` job status; the result is an `image`/`video` block that opens in the canvas.
- `GET /api/generated` (own items), `DELETE /api/generated/{id}`. Admin: `GET /api/admin/generated?pending=1`, `POST /api/admin/generated/{id}/approve {section_id, caption}` → copies the file to `content-public`, creates a shared `media_assets` row (source `naadi-ai-generated`, caption ends with the AI label) and a figure in a new `chapter_section_revisions` row (origin `admin`). S19 later moves this list into its approval queues.
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `generated_media` gains `status ('queued'|'generating'|'ready'|'failed'|'paused')`, `error`, `approved_by`, `approved_at`, `media_asset_id`. Client: select + delete own rows only; `approved_for_publish` and approval columns are admin/API-only.
- `config/sources.yaml` + seed: `naadi-ai-generated` (Naadi-owned output; FLUX.1-schnell Apache-2.0; `allow_ai_ingest` false so generated images never enter RAG). `app_config` flag `generation`; `ui_copy` `generation.*`.
### Web
- Canvas kind `generated_media` and answer blocks: image/video with the label chip "AI-generated illustration — not clinical reference" always visible (word + icon, violet), alt text prefixed with the label, progress bar with words, paused state with reset time, failed with retry.
- Library "Generated" filter (own items, delete). Admin approve dialog (choose section, caption) on the admin list.
### Guardrails (review blockers)
- No placeholders or fake media: no sample images in the repo; tests use tiny generated fixtures. Every UI string a `ui_copy` key; pydantic → OpenAPI → zod.
- Generation runs only on `naadi-gen` ZeroGPU; prompts scrubbed of PII; no LLM call is added. A generated item never becomes a shared figure without admin approval, and always references `naadi-ai-generated`.
- Lime = one next action per view, violet = generated output; status never colour-only.

## Tests
- Unit (fake `gradio_client`): caps, quota-exceeded → paused with reset time, not-configured path, prompt refusal rules, scrubbing, storage paths, approval creating `media_assets` + revision.
- db: RLS (user B cannot see A's media), client cannot set `approved_for_publish`, approval is admin-only.
- Space: `deploy/gen-space` unit test of input validation on CPU (models mocked); one live image smoke on `workflow_dispatch` only (spends quota).
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`, generation stubbed at the API): ask for an image → confirm → progress → labelled image in the canvas; quota stub exhausted → paused state; admin approves → figure in the section with its label.
## Acceptance
1. An image in seconds and a ≤ 5 s video with progress, from the private ZeroGPU Space; never billed.
2. Quota exhausted → a clear paused state with the reset time; every item labelled AI-generated.
3. Nothing becomes a chapter figure without admin approval; zero axe violations / console errors.

## Decisions (lead to check)
- Token: a fine-grained `HF_GEN_TOKEN` scoped to `naadi-gen` (PROGRESS: until it is set, generation shows "not configured").
- New `sources` row `naadi-ai-generated`, `allow_ai_ingest: false`. Approved media copy into `content-public`.
- No pixel watermark; the label travels as a chip, alt text and caption. Video is off if the LTX-Video licence or ZeroGPU fit fails.
