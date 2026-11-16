# Segment 20 — M6: `youtube_map` cron + approvals, IFrame player with timestamped notes, Anki `.apkg` import/export

**Implementer:** `implementer-sonnet` (spec'd tools exist: `tools/youtube_mapper.py`, S15's pipeline; bounded). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** G (with S19, S21). Contracts on the ledger: with `seg19` the queue registry (`youtube_candidates`); with `seg15` the extractor + placement registries (`anki` input kind → `flashcards` placement). Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg20`).
**Depends on:** S02 (`media_assets`, `section_media`, `flashcards`, `card_reviews`, `notes.anchor_type 'video'`), S04 (Aegra crons), S06 (video node: today a click-to-load `youtube-nocookie.com` iframe facade in `apps/web/src/content/MediaNodes.tsx`), S07 (fastembed engine), S10 (cards + FSRS state), S13 (notes API), S15 (ingest registries, Library), S19 (queues hub, YouTube add dialog).
**HANDOFF refs:** §5.4 (IFrame Player API unmodified, timestamped notes `anchor_pos {t}`, chapter markers from `section_media.start_s/end_s`, speed control), §7.1 (YouTube Developer Policies: no download/re-host, no hidden branding, refresh or drop metadata within 30 days, "YouTube" source + Terms link, privacy disclosure, nothing beyond pressing play; quota via `playlistItems.list` + `videos.list`), §7.4 + §8 Cards row (Anki import/export round-trips), §10 (weekly mapper refresh). `config/youtube_channels.yaml` (only `include: true` and `verified: true` channels). `docs/AGENTS.md` → `youtube_map`, Decks and banks. Boards: `design/Lesson.dc.html` (video player, chapter markers, notes), `design/Tablet.dc.html`, `design/Cards.dc.html`, `design/Library.dc.html` (Anki upload tile).

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `video_candidates` (shared, backend-only, admin select): `media_id` → `media_assets`, `section_id`, `taxonomy_ids`, `score`, `matched_terms`, `status ('pending'|'approved'|'rejected'|'stale')`, `reviewed_by`, `reviewed_at`, `created_at`. Candidate videos are `media_assets` rows with `kind 'video_youtube'`, `source_id 'youtube'`, `review_status 'unreviewed'` until approved.
- `flashcards` gains `tags text[]`, `anki_guid text`, `anki_note_type text`; unique `(owner_user_id, anki_guid)` for idempotent re-imports.
- `app_config.youtube`: `min_score: 0.42`, `max_candidates_per_section: 3`, `refresh_days: 25`; flag `anki`; `ui_copy` `video.*`, `anki.*`.
### `youtube_map` graph (Aegra cron, weekly Sunday 05:17 UTC; port `tools/youtube_mapper.py` logic into `naadi_api/youtube/`, the tool stays a thin CLI over it)
- Official `google-api-python-client` (pin) with `YOUTUBE_API_KEY` via `settings.py`: per allowlisted channel `channels.list` → uploads playlist → `playlistItems.list` (new since last run) → `videos.list` (`snippet`, `contentDetails`, `status`); keep `status.embeddable` and public only. Quota ledger per run (≤ 2,000 units; stop early).
- Scoring offline, no LLM: fastembed similarity of title + description against each section's heading + taxonomy labels, boosted by channel `subjects`; top `max_candidates_per_section` above `min_score` become `pending` candidates.
- **Refresh**: metadata older than `refresh_days` is re-fetched; videos now private, deleted or not embeddable are unlinked from `section_media`, marked `rejected`/`stale`, and their stored title/description dropped (30-day rule). Nothing is downloaded; no transcripts or thumbnails stored (thumbnails load from YouTube's CDN at view time).
- Approve (S19 queue): sets `media_assets.review_status 'approved'`, creates `section_media` with optional `start_s`/`end_s` chosen in the dialog; reject keeps the id so it is not proposed again.
### Player (`apps/web/src/video/`)
- Replace the plain iframe with the official **IFrame Player API** (`https://www.youtube.com/iframe_api`, host `https://www.youtube-nocookie.com`, `origin` set, `referrerpolicy="strict-origin-when-cross-origin"`), unmodified chrome, no overlays on the player, no autoplay; the click-to-load facade stays (one press loads and plays: `playVideo()` after `onReady`). Source line "YouTube" + Terms link under each player; Settings → About & privacy discloses YouTube API use.
- Chapter markers from `section_media.start_s/end_s` as a list beside the player (seek on tap); speed control via `setPlaybackRate` with the rates the player reports.
- **Timestamped notes**: "Note at 12:22" creates a note `anchor_type 'video'`, `anchor_id` media id, `anchor_pos {t}` (S13 API); notes list sorted by `t`, tap seeks. The assistant page context gets `{media_id, t}` (S14).
### Anki (`naadi_api/anki/`)
- **Import** (registered as S15 extractor `anki` + placement `flashcards`): `.apkg` ZIP with `collection.anki21b` (zstd, via `zstandard` pin) or `collection.anki21`/`collection.anki2` (SQLite, stdlib `sqlite3`, opened read-only from a temp file, size caps from `ingest`); media map as legacy JSON, or the newer zstd protobuf manifest read by a minimal decoder (file name + index only); if the manifest can't be read, cards import without media and the report says how many images were skipped. Basic, Basic-and-reversed and Cloze note types → front/back HTML (cloze rendered per ordinal); HTML sanitised (same allowlist as notes); images → WebP in `user-uploads`; audio dropped (Storage budget, reported). Decks keep their names (`::` → " / "), tags kept. Cards start as **new** in FSRS (Anki scheduling is not imported). Re-import is idempotent by `anki_guid`.
- **Export**: `GET /api/anki/export?deck=` → `.apkg` via `genanki` (MIT, pin) with stable note GUIDs (`anki_guid` or derived from card id), the student's own cards only (`user`, `assistant`, `anki_import`), images bundled. Question-linked shared cards are excluded unless attempted.
- Web: Library Anki tile and Cards → "Import deck" / "Export deck" (deck picker); import report (cards, decks, skipped media).
### Guardrails (review blockers)
- No placeholders, sample videos or decks in the repo (tests build tiny `.apkg` fixtures in code). Every UI string a `ui_copy` key; pydantic → OpenAPI → zod.
- YouTube only via the official IFrame player; every video is a `media_assets` row with `source_id 'youtube'`; nothing downloaded. Imported decks stay private (owner set) and never enter shared content; no commercial deck text enters RAG beyond the owner's own index.
- No LLM call is added. Lime = one next action per view; status never colour-only.

## Tests
- Unit: mapper with recorded API responses (embeddable filter, quota stop, scoring, refresh unlink/drop), approval creating `section_media`; `.apkg` parse for anki2/anki21/anki21b fixtures, cloze, sanitiser, idempotent re-import; export → import round-trip equal on front/back/deck/tags/GUID.
- db: RLS (user B cannot see A's imported cards or media); candidates closed to students; unapproved videos never in a student response.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): admin approves a candidate → video appears in the section; play (stubbed IFrame API in CI, real on the device checklist) → note at a timestamp → tap seeks; speed change; import an `.apkg` → review a card → export → re-import is a no-op.
## Acceptance
1. Mapper lists channel uploads, keeps embeddable ones, writes candidates; approvals link them; metadata refreshed or dropped within 30 days.
2. IFrame player unmodified with chapter markers, speed and timestamped notes.
3. Anki import/export round-trips; matches Lesson/Cards/Library boards; zero axe violations / console errors.

## Decisions (lead to check)
- New backend-only `video_candidates`; `flashcards` gains `tags`, `anki_guid`, `anki_note_type`.
- Import uses stdlib SQLite + `zstandard`, export uses `genanki` (not the heavy official `anki` wheel); Anki scheduling is not carried over (cards start new in FSRS); audio dropped.
- The player keeps the click-to-load facade on `youtube-nocookie.com`, but through the IFrame Player API so timestamps and speed work.
