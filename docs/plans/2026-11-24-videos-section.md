# Videos section — plan (2026-11-24)

Spec: the user's request in this session plus `handoff/HANDOFF.md` §5.4 Video, the `youtube` and `wikimedia-commons`
rows in `config/sources.yaml`, and `CLAUDE.md`. Integration facts (file:line map of every touch point): `docs/plans/videos-integration-map.md`.

Goal: a top-level **Videos** section with a large collection of easy-to-follow, visual (sketch / whiteboard /
mnemonic style) free videos covering USMLE Step 1 and Step 2 CK, embedded legally, browsable by step / subject /
chapter, searchable, with per-student "watched" self-tracking, and wired into the AI assistant.

## Global Constraints

- Legal sources only. YouTube through the existing official-player bridge (`apps/web/src/video/*`, the player Space),
  never a direct youtube iframe in the app origin. Wikimedia Commons files only when the file's own licence
  (Commons API `imageinfo` `extmetadata.LicenseShortName`) is one of: `CC0`, `Public domain`, `CC BY *`, `CC BY-SA *`;
  store the licence snapshot and the attribution string built from `attribution_template` of the `wikimedia-commons`
  sources row. No Sketchy, Pixorize, Picmonic, Boards & Beyond or any other paid platform's own streams, pages,
  thumbnails or iframes. No direct links to sketchy.com.
- The 938 topic names extracted from the user's atlas are a coverage map only (topic title, subject, unit, chapter,
  step, core). Never store or show the word "Sketchy" in the UI, data or DB; never store the atlas's Cloudflare
  stream uids or thumbnails.
- YouTube Developer Policies (sources.yaml `youtube` notes): do not commit YouTube API metadata (titles,
  descriptions, durations, thumbnails) to the repo. The repo stores YouTube ids plus Naadi-authored fields only;
  titles/durations reach the DB through the existing 30-day refresh (`naadi_api/youtube/mapper.py` `_refresh`).
  "Watched" is the student's own checklist: no XP, streaks, scores or rewards for watching.
- Colour roles: lime = the one next action per view, violet = assistant, paper = student. Status never colour-only
  (use a word / icon + text, e.g. Chip tone "done" with "Watched").
- iPad Safari is the primary target: test portrait + landscape and Playwright WebKit. Touch targets >= 44px.
- All UI strings in `config/app_config.yaml` `ui_copy`, listed in `REQUIRED_UI_COPY_KEYS`, defaults JSON regenerated.
- DB changes only via a new Alembic migration (0040) with RLS in the same migration; update `db/schema.sql`,
  `tests/db/fixtures.py` USER_TABLES, `tests/db/live_check.py`, `tests/auth/test_auth_db.py` `_user_rows`.
- Seed importer stays idempotent (upsert by id, second run 0 changes), honours `admin_edited_at` guard.
- Every route has a rate-limiter bucket. Run `pnpm gen:contracts` after API changes and commit generated files.
- Pin exact versions; add no new dependencies unless unavoidable.
- Commit messages never mention Claude / AI sessions; no Co-Authored-By lines.

## Tasks

### Task 1: Video catalog builder and data

Files: `tools/video_catalog.py` (new), `config/video_channels.yaml` (new), `data/video_topics.json` (new),
`data/videos.json` (new), `apps/api/tests/tools/test_video_catalog.py` (new, or nearest existing tools test dir),
`package.json` lint/format lists (add the new tool path like the other tools).

- Input topic map: `/tmp/claude-0/-home-user-naadi/64948565-4626-50de-bf7e-4f99e65844e2/scratchpad/topics.json`
  (938 topics, fields id/title/subject/unit/chapter/step `S1|S2`/core) and
  `.../scratchpad/atlas_youtube.json` (158 YouTube videos: youtube_id, channel, subject, chapter, step, core).
  Extend coverage for subjects the map lacks or is thin on (Immunology, Behavioral Science, Psychiatry,
  Emergency & Family Medicine, Neurology, Biostatistics, plus Step 2 CK cardiology/pulm/renal/GI/endo/heme-onc/ID
  breadth) by adding topics from Naadi's own taxonomy (`data/taxonomy.json`, USMLE 2026 outline) so every
  taxonomy leaf relevant to Step 1 / Step 2 CK has a topic. Topic ids: kebab-case slugs, stable.
- `config/video_channels.yaml`: channels the builder crawls, each `{handle, channel_id (resolve via channels.list
  forHandle if null), name, style: sketch|whiteboard|animation|lecture, steps: [step1, step2ck], subjects}`.
  Include at least: Dirty Medicine, Pixorize, Physeo, Armando Hasudungan, Osmosis from Elsevier, Ninja Nerd,
  Medicosis Perfectionalis, Zero To Finals, Speed Pharmacology, Randy Neil MD, Strong Medicine, MedCram,
  Doctor High Yield MD, Rhesus Medicine, Khan Academy Medicine, Dr. Najeeb Lectures, Board Ready Psych,
  Memorable Psychiatry and Neurology, Neuroscientifically Challenged, The Noted Anatomist, Conan Liu MD,
  Sam Webster, Lecturio Medical, Catalyst University, Handwritten Tutorials. Channels that fail to resolve are
  skipped and reported, not fatal.
- YouTube: reuse `naadi_api.youtube.client` (`GoogleYouTube`, `Quota`, `uploads_playlist`, `keep`,
  `iso_duration_s`); add a `channel_by_handle` method there if needed (1 unit). Enumerate uploads (1 unit/50),
  `videos.list` (1 unit/50), keep embeddable + public + processed, drop Shorts (< 120 s), live, and > 75 min.
  Quota budget flag, default 3000 units.
- Wikimedia Commons: MediaWiki API with a generic User-Agent `MedEduCatalogBuilder/1.0 (educational video
  catalog)`, `maxlag=5`, >= 1 s between requests, retry on 429 with backoff. Search `filetype:video` in File
  namespace for Osmosis videos (`*-osmosis-video.webm`, `VideoWiki ... (Osmosis)`) and medical categories;
  fetch `imageinfo` (url, mime, size, extmetadata: LicenseShortName, LicenseUrl, Artist, ImageDescription) and
  apply the licence allowlist. Commons file text metadata may be committed (open licence).
- Matching: embed video titles and topic titles with the project's local fastembed engine
  (`naadi_api.embeddings.engine.get_engine`) and match each topic to its best videos: top 3 per topic above a
  similarity threshold (tune so precision is high; print a sample for eyeballing), prefer style sketch >
  whiteboard > animation > lecture and the step/subject fit of the channel. Every atlas YouTube video is
  included and attached to its atlas chapter's topic.
- Output `data/video_topics.json`: `{"version":1,"subjects":[...ordered], "topics":[{id,title,subject,unit,chapter,
  step:"step1"|"step2ck",core,sort,media_ids:[...]}]}`. Output `data/videos.json`:
  `{"version":1,"videos":[{id, provider:"youtube"|"commons", youtube_id|null, file_url|null, file_page|null,
  channel_id|null, channel_name, style, subject, step, topic_ids:[...], core, licence|null, attribution|null,
  label, duration_s (commons only)}]}` where id is `yt-<lowercased id>` (same as `naadi_api.youtube.client.media_id`)
  or `wm-<slug of file name>`, and `label` is a Naadi-authored short label (from the topic title), NOT the YouTube
  title. No YouTube titles/durations in the committed JSON.
- Run it for real (env has YOUTUBE_API_KEY; use `uv --directory apps/api run python ../../tools/video_catalog.py`).
  Print counts: videos per provider/channel/step/subject, topics covered vs gaps. Target: >= 90% of topics covered.
- Unit test with fakes (`apps/api/tests/youtube/fakes.py` FakeYouTube) for: filtering rules, Commons licence
  allowlist, matching picks/threshold, deterministic output ordering.

### Task 2: Migration 0040 — video catalog schema

Files: `apps/api/migrations/versions/0040_videos.py`, `db/schema.sql`, `apps/api/tests/db/fixtures.py`,
`apps/api/tests/db/live_check.py`, `apps/api/tests/auth/test_auth_db.py`, plus any courses.py `MediaOut.kind` Literal.

- `media_assets.kind` check gains `'video_file'` (an open-licence HTML5 video file served from its host; `url` =
  file URL). Add nullable `licence text` and `page_url text` columns to `media_assets` only if no existing column
  fits (check `attribution`, `url`, `metadata`); keep it minimal.
- `video_topics` (shared, read-only to clients): `id text pk, title text not null, subject text not null,
  unit text, chapter text, step text not null check (step in ('step1','step2ck')), core boolean not null default
  false, sort int not null`. RLS: select to anon+authenticated using true (copy a shared-read pattern), no client
  writes, service_role all.
- `video_catalog` (shared): `media_id text pk references media_assets(id) on delete cascade, subject text not null,
  step text not null check (...), style text not null check (style in ('sketch','whiteboard','animation','lecture')),
  core boolean not null default false, topic_ids text[] not null default '{}', provider text not null check
  (provider in ('youtube','commons')), sort int not null`. RLS read: only when the media row is approved (copy
  `section_media` read policy style).
- `video_watches` (own rows, copy `0003_reading_positions` exactly: 4 policies, force RLS, column grants):
  `user_id uuid references auth.users on delete cascade, media_id text references media_assets(id) on delete
  cascade, watched_at timestamptz not null default now(), primary key (user_id, media_id)`; insert check requires
  the media be an approved video.
- Indexes for (step, subject, sort) and gin on topic_ids.
- downgrade reverses everything. Update the doc/test lists named in Global Constraints and run the DB tests
  (`docker` is available; follow how CI starts the stack: `.github/workflows/ci.yml`, `scripts`/`stack.sh`).

### Task 3: Seed group `videos`

Files: `apps/api/naadi_api/seed/data.py`, `apps/api/naadi_api/seed/importer.py`, seed tests, `docs/RUNBOOK.md`.

- Load `data/video_topics.json` + `data/videos.json` into `SeedBundle` (pydantic `extra=forbid`, problems listed
  together; cross-check every topic media id exists and every video topic id exists; source ids accepted).
- Group `videos` after `chapters` in `GROUPS`: upsert `media_assets` (YouTube: kind `video_youtube`, source
  `youtube`, youtube_id, channel_id, title = `label`, `metadata_fetched_at = '1970-01-01T00:00:00Z'` on insert so
  the existing refresh fills real title/duration on its next run, review_status `approved`, embeddable true,
  attribution from the sources template once known; Commons: kind `video_file`, source `wikimedia-commons`,
  url, title, duration, attribution, licence, approved), `video_topics`, `video_catalog`. Guarded by
  `ADMIN_EDIT_GUARD`; `keep_existing` for fields the refresh owns (title, duration_s, metadata_fetched_at,
  attribution for YouTube) so a re-seed never undoes a refresh. Topics/catalog rows not in the data are deleted
  (seed-owned tables). Report counts under new keys; `expected_counts`, `verify`, `_COUNT_GROUP` updated.
- Idempotency test: second run 0 changes. Document in RUNBOOK: after seeding run
  `tools/youtube_mapper.py --refresh-only` once to fill titles.

### Task 4: Videos API

Files: `apps/api/naadi_api/videos/{__init__,models,routes}.py` (new), `main.py`, `packages/shared/openapi.json`
+ `src/gen` (regenerated), API tests.

- `GET /api/videos` (operationId `listVideos`): query `step` (step1|step2ck|all), `subject`, `q` (title/topic/
  chapter/channel search, ILIKE + simple ranking), `channel`, `style`, `unwatched` bool, `core` bool,
  `limit` (<= 200, default 60), `cursor`/offset. Returns items `{media_id, provider, youtube_id, file_url, title,
  channel_name, duration_s, style, subject, step, core, topics:[{id,title,chapter}], attribution, watched}` +
  `next` + `total`. Runs as the user (RLS).
- `GET /api/videos/overview` (`getVideosOverview`): per step+subject counts (videos, topics, covered topics,
  watched), channel list with counts, ordered subjects.
- `GET /api/videos/topics` (`listVideoTopics`): topics for a step/subject with their media ids and covered flag.
- `PUT /api/videos/{media_id}/watched` and `DELETE` (`markVideoWatched` / `unmarkVideoWatched`), 204.
- Rate-limit buckets, error model, `private, no-store` for per-user responses. DB tests for RLS behaviour.

### Task 5: Assistant integration

Files: `apps/api/naadi_api/agent/tools/media.py` (+ new `find_videos` tool if cleaner), assistant page-context
resolver, `apps/web/src/assistant/{ask.ts,AssistantHost.tsx,blocks.tsx,protocol.ts}`, tests.

- `suggest_video` searches the catalog (title, topic titles, chapter, subject; rank exact topic > title words),
  returns up to 3 video blocks, supports `video_file` (provider `commons`, `file_url`, attribution).
  Add `find_videos(query, step?)` returning a short list the model can cite, if the tool registry pattern makes it
  cheap; ui_copy label keys for new tools.
- Web `VideoBlock` renders both providers (YouTube via existing `YouTubeVideo`/Facade; commons via the new file
  player from Task 6 — if Task 6 lands later, Task 5 adds the shared `FileVideo` component itself).
- `AssistantAskContext` gains `{type:"video", id, t?}`; page context on the Videos player registers
  `type:"video"`, `id: media_id`, `title`, `readVideo: currentVideoPosition`. The server page resolver gives the
  model the video's title, channel, topics and current time.
- Never let the assistant reveal question answers (existing rules unchanged); assistant stays disabled in exam mode.

### Task 6: Videos web section

Files: `apps/web/src/videos/*` (new: routes.tsx, VideosPage.tsx, VideoCard.tsx, VideoPlayerPane.tsx, FileVideo.tsx,
api.ts, tests), `apps/web/src/router.tsx`, `apps/web/src/shell/icons.tsx`, `config/app_config.yaml`
(feature flag `videos`, nav entry after Learn/Practice, ui_copy keys), `apps/api/naadi_api/config_api/schemas.py`
REQUIRED keys, `packages/shared/ui-copy.defaults.json`, `apps/api/naadi_api/security.py` CSP media-src
`https://upload.wikimedia.org` (+ the tests/docs that assert directives: tests/test_security_headers.py,
tests/courses/test_sandbox_headers.py, apps/web/e2e/video.spec.ts, docs/SECURITY.md).

- Follow the existing design system (packages/ui tokens/components, the Library and Search boards in `design/` as
  visual reference). Layout: page header with step tabs (Step 1 · Step 2 CK · All) and a quiet "N of M marked
  watched" line (no gamification), search field, filter chips (style, channel, core, unwatched), subject jump bar;
  grid of video cards grouped by subject (thumbnail: YouTube `i.ytimg.com/vi/<id>/hqdefault.jpg` — allowed in
  img-src — or a neutral poster for Commons; title, channel, duration, style chip, core marker with text,
  "Watched" toggle). Selecting a card opens a player pane (side pane in landscape/desktop, full-width sheet in
  portrait/phone) with the player, attribution + licence, topics, "Mark watched", "Ask Naadi about this video"
  (violet, opens the assistant with a prefilled draft and video context), and "More on this topic" list.
  A "Topics" view lists the coverage map per subject with covered/gap state (text, not colour alone).
- `FileVideo`: native `<video controls preload="none" playsinline>` with click-to-load poster, attribution line,
  publishes position via `publishVideoPosition`.
- Feature gating like planner routes; URL search params hold step/subject/q/filters/selected video.
- Unit tests (vitest + testing-library) for filtering UI, watched toggle, player pane, empty/error/loading states.

### Task 7: End-to-end, devices, visual check

Files: `apps/web/e2e/videos.spec.ts` (new) and fixtures as the existing e2e specs do.

- Playwright WebKit + Chromium: open Videos from nav, switch steps, search, filter, open a video (YouTube facade and
  a Commons file), mark watched persists, ask assistant opens dock with context. iPad portrait + landscape and
  phone screenshots reviewed against the design system; fix defects found.
