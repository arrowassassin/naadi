# Segment 18 — M5: Offline week (PWA, "download this week", Dexie cache, outbox sync, conflicts)

**Implementer:** `implementer-opus` (named high-risk: offline sync; a mistake loses the student's work silently or leaks answers into the device cache). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** F (with S16, S17; lands last in the wave). Post the **outbox entity list** on the ledger on day one and get an `ack` from `seg16` and `seg17`; claim `apps/web/src/sw.ts` (S16 creates it with push handlers; this segment adds caching). Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg18`).
**Depends on:** S06 (sections, figures, reading positions), S08/S09 (attempts API idempotent per (block, question); clock offset), S10 (`POST /api/cards/{id}/review` idempotent on a client review id), S12b (ink `changes` endpoint, Dexie `inkPending`, `mergeInkItems`), S13 (notes, highlights, notebooks), S16 (`sw.ts`, manifest, plan item status LWW), S17 (pathway task status + costs LWW), `apps/web/src/practice/outbox.ts` (localStorage queue to migrate).
**HANDOFF refs:** §5.2 (install prompt, `navigator.storage.persist()`, "Download this week" in IndexedDB, own videos only, outbox, LWW per row, ink by stroke id), §5.4 (YouTube can't be cached), §6.3 (no answers before answering), §8 Settings row (offline download size), §11 M5 ("Airplane-mode study day works end to end on iPad"). Boards: `design/Settings.dc.html` (Offline group), `design/Main.dc.html` + `design/PhoneToday.dc.html` (sync state), `design/Nav.dc.html`, `design/Chapter.dc.html` (save states).

## Scope
### Outbox (`apps/web/src/offline/`, Dexie 4.4.7 database `naadi-v1`, per user)
- One `outbox` table: `{op_id (uuidv7), user_id, entity, key, method, body, client_updated_at_ms, queued_at, attempts, last_error}`; coalesced per `(entity, key)` where the entity is LWW. Entities and their server rule:
  - `attempt` → S08/S09 endpoint, first answer stands, idempotent per (block, question); replays after the section deadline are rejected and shown as "Not counted: time was up".
  - `card_review` → append-only, idempotent on the review id; the server orders by `reviewed_at`.
  - `ink` → 12b's `changes` endpoint (the `inkPending` map moves into the outbox; merge by item id, unchanged).
  - `note`, `highlight`, `notebook`, `plan_item` (status), `user_pathway_task`, `cost`, `reading_position` → LWW per row on `client_updated_at_ms` (clamped to server time + 5 min; tie → server wins); deletes are tombstone ops that lose to a newer edit.
- Never queued: vault uploads, ingest uploads, assistant messages, generation, admin writes (disabled offline with a reason).
- Replay on `online`, `visibilitychange` visible, app start and every 30 s while pending; FIFO per entity, parallel across entities; exponential backoff; 4xx other than 409/429 parks the op as `failed` with Retry / Discard in the sync sheet. The practice localStorage queue is migrated once and deleted.
- Each LWW response returns the stored row; the client adopts it. When the server's version won, the item shows "Changed on another device" for that session (word + icon), and the losing local version is kept in a `conflicts` table for 7 days, viewable from the sync sheet.
### Server changes (one migration with RLS for the new tables in the same file; update `test_rls_coverage.py` + `live_check.py`)
- `client_updated_at_ms bigint` on `notes`, `notebooks`, `reading_positions` (S16/S17 add theirs on `plan_items`, `user_pathway_tasks`, `costs`); a shared helper `naadi_api/sync/lww.py` (`apply_lww(table, key, row, client_ms)` as one `insert … on conflict … where excluded.client_updated_at_ms > stored`) used by every LWW endpoint, and `sync_tombstones` (user table: entity, key, `client_updated_at_ms`, 90-day GC) so a delete replayed after an edit resolves the same way on every device.
- Idempotency: every write endpoint accepts an `Idempotency-Key` header (`op_id`); results cached 24 h in `sync_ops` (user table, backend-only, GC daily).
- `GET /api/offline/week?start=` → manifest for the 7 plan days: sections (TipTap JSON + revision), figure URLs (`content-public`), own-video URLs ≤ 1080p, questions of planned blocks **without** `answer`, `answer_html`, `explanation_html`, `why_not_html`, `educational_objective` (post-attempt fields only for questions already attempted outside an active block), due cards (attempt gate applied), the student's notes, notebooks and ink layers for those surfaces, plan items, pathway task status. Byte estimate per part. Same filtering code as the online endpoints (shared serializers), proven by tests.
### Web
- **Service worker** (`sw.ts`, Workbox via `vite-plugin-pwa` `injectManifest`): precache the app shell, fonts, icons and the pdf.js worker; runtime CacheFirst for `content-public` figures (bounded, 300 entries); **no `/api/*` response is cached by the SW** (data lives in Dexie only); navigation fallback to the shell. Update flow: "New version ready · Reload" (no forced reload mid-exam).
- **Dexie cache** tables: `sections`, `media`, `questions`, `cards`, `notes`, `notebooks`, `inkLayers`, `planDays`, `pathway`, plus `outbox`, `conflicts`, `meta`. TanStack Query reads fall back to Dexie when offline (a `offlineFirst` query helper per entity); writes always go through the outbox, online or not.
- **"Download this week"** (Settings → Offline and a Today action): size preview from the manifest, progress with words, "Ready offline until Sun" state, remove download. On first launch from the Home Screen: install hint + `navigator.storage.persist()`; Settings shows persisted yes/no and `navigator.storage.estimate()` usage.
- Global sync indicator (icon + word): "Offline · 12 changes will sync", "Syncing…", "All changes saved", "2 need attention". YouTube blocks offline show "Needs a connection" (never cached); assistant and search show their offline states.
- Tutor mode offline: the answer is recorded and the question shows "Explanation after you reconnect"; it is fetched from the server once the attempt syncs. Timed/exam offline: S09's cached offset keeps the timer; answers queue.
### Guardrails (review blockers)
- No answer, explanation, why-not or objective is ever written to Dexie, Cache Storage or the outbox before an attempt syncs; a test scans every store after a full download.
- No placeholders or fake data; every UI string a `ui_copy` key (`offline.*`, `sync.*`); pydantic → OpenAPI → zod. No LLM or web call is added; note and ink bodies never logged.
- Lime = one next action per view; sync status never colour-only.

## Tests
- Unit (vitest + fake-indexeddb): coalescing, FIFO per entity, backoff, parking, migration of the practice queue, LWW adoption, conflict retention, tombstone vs edit in both orders, Dexie fallback.
- pytest: `apply_lww` golden vectors shared with the TS client (both orders, ties, clamp, tombstones), idempotency keys, manifest filtering (no hidden field, attempt gate, active block), byte estimates.
- db: RLS on `sync_tombstones`/`sync_ops`; LWW endpoints reject other users' keys.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): **airplane-mode study day**: download the week → `context.setOffline(true)` → reload (shell from the SW in Chromium/WebKit) → read a section, highlight, ink, answer 10 tutor questions, review 10 cards, tick plan items → online → everything synced, server state equals the device; two contexts edit the same note offline → LWW result on both with the conflict notice; delete vs edit; ink merged by stroke id; leak scan of IndexedDB + Cache Storage. Device checklist: installed iPad PWA in airplane mode.
## Acceptance
1. An airplane-mode study day works end to end; the outbox replays without loss or double-counting.
2. LWW per row and ink merged by stroke id, with conflict tests in both orders; no answer leaks into any device store.
3. Matches the Settings/Today boards; one lime CTA per view; zero axe violations / console errors.

## Decisions (lead to check)
- **Contract change:** every LWW endpoint takes `client_updated_at_ms` and an `Idempotency-Key`; new user tables `sync_tombstones` and `sync_ops`. S13's notes API and S06's reading positions change accordingly.
- The SW caches no API responses; Dexie is the only offline data store. Tutor-mode explanations are not available offline (they arrive after sync).
- Attempts are first-answer-stands, card reviews append-only; only the other entities are LWW. Conflict losers are kept 7 days on the device.
