# Segment 12b — M3: Ink persistence and cross-device sync on sections, questions and cards

**Implementer:** `implementer-opus`. Reason: this is the merge/conflict contract that S18 (offline, named high-risk) and S13/S15 build on. Mistakes here lose the student's ink silently or resurrect deleted strokes, and the merge has to match `mergeInkItems` in `packages/shared` exactly. **Reviewer:** `reviewer-opus`.
**Wave:** C (parallel with S07, S08, S10). Contracts on the ledger: surface ids and mount points with `seg06`/`seg08`/`seg10`. Claim the next free Alembic revision. Local e2e: `E2E_PORT=7942` (uses 7942–7944), `NAADI_PG_PORT=54442`, `NAADI_STORAGE_PORT=54542`, containers `pg-seg12b` / `st-seg12b`.
**Depends on:** S02 (`ink_layers`), S03 (profiles.settings, shell pen-surface registration), S06 (Reader + `ContentRenderer`), S08 (question view), S10 (card review), S12a (`packages/ink` engine, `InkCanvas`, `@naadi/shared` ink schema: `InkItem`, `InkDiff`, `recordInkDiff`, `mergeInkItems`, `uuidv7`).
**HANDOFF refs:** §5.2 (outbox, LWW per row, ink merged by stroke id), §5.3 (surface-relative points, margin notes with a ProseMirror position), §8 rows Lesson/Chapter ("ink works on every page"), Ink, Settings (pen-only toggle). Boards: `design/Ink.dc.html`, `design/Chapter.dc.html`, `design/Tablet.dc.html`, `design/PhoneChapter.dc.html`, `design/Qbank.dc.html`, `design/Cards.dc.html`, `design/Settings.dc.html` (Pencil & ink group).

## Scope
### Data (one Alembic migration with RLS; add `ink_items` to `USER_TABLES` in live_check + the RLS coverage test)
- `ink_items`: `user_id`, `layer_id` → `ink_layers.id` (cascade), `item_id uuid`, `updated_at_ms bigint`, `deleted bool`, `item jsonb` (one `InkItem`), `seq bigint` (from a sequence, bumped on every write); PK `(layer_id, item_id)`; index `(layer_id, seq)`. `authenticated`: select only (writes through the API so every item is validated and merged).
- `ink_layers` keeps one row per (user, surface_type, surface_id) and gains `rev bigint` (max `seq`), `item_count int`, `content_hash text`. Drop the unused `strokes` column (nothing was persisted before 12b); update `db/schema.sql` and the comment in `packages/shared/src/ink.ts`.
- `profiles.settings.ink`: `pen_only`, `predict`, last tool/colour (pydantic-validated).
### API (pydantic → OpenAPI → zod; `naadi_api/ink/`)
- Pydantic `InkItem` models mirror `packages/shared/src/ink.ts`; a shared fixture set (valid and invalid items, limits, UUIDv7, precision) must pass/fail identically in pydantic and zod, and the models are checked against `packages/shared/ink.schema.json`.
- `GET /api/ink/{surface_type}/{surface_id}?since=` → `{layer_id|null, rev, items, reset, server_now}`. Full load omits tombstones; `since` returns everything with `seq > since` including tombstones; `reset: true` when `since` is older than the tombstone horizon.
- `POST /api/ink/{surface_type}/{surface_id}/changes` `{base_rev, items[]}` (upserts and tombstones, ≤ 500 items, ≤ 2 MB) → one `insert … on conflict do update … where` newer, using `mergeInkItems`' order: higher `updated_at_ms` wins, on a tie a tombstone wins, on a full tie the stored row stays. Returns `{rev, applied, rejected[{id, reason}], remote: items with seq > base_rev}`. Idempotent by construction (resending the same items is a no-op). `updatedAt` more than 5 minutes ahead of server time is clamped to server time. More than `INK_MAX_ITEMS` live items → 409.
- Surfaces this segment mounts: `section` (section id, visible to the caller), `question` (question id; the layer covers stem and options only, never the explanation), `flashcard` (card id visible to the caller). The endpoint also validates `exam_scratch` (`{block_id}:{n}`, own block), `notebook_page` (own note id) and `pdf_page` (`{upload_id}:{page}`, own upload) for S09/S13/S15. Anything else → 404 without saying whether it exists.
- Daily job: hard-delete tombstones older than 90 days. Never log request bodies (ink is note content).
### Web (`apps/web/src/ink/persist/`, plus a small engine extension in `packages/ink`)
- `useInkLayer(surfaceType, surfaceId)`: loads the layer (TanStack Query), feeds `InkCanvas`, records every `onStrokesChange` diff with `recordInkDiff` into a pending map (latest version per item id), persisted first to Dexie 4.4.7 (`inkPending` table), then flushed after 800 ms of idle or 50 items, on `visibilitychange` hidden and `pagehide` (`fetch` with `keepalive`, chunked under 64 KB). After a reload the pending items are merged in and resent. S18 later folds `inkPending` into the general outbox, using the same POST.
- Remote changes (refetch with `since` on focus, `visibilitychange` visible and every 30 s while mounted): `InkEngine.applyRemote(items)` merges by LWW without clearing undo history or adding undo steps (new engine API, unit-tested). Undo after a remote edit re-stamps the local version (it wins, as the engine already does).
- Reflow anchoring on `section` surfaces: on commit each item gets `anchor {pm, dy}` for the top-level block under its top edge (positions computed from the TipTap JSON node sizes in `ContentRenderer`, which exposes block offsets); the engine adds an `anchorOffset(pm)` resolver, so ink drawn on iPad landscape sits beside the same paragraph on phone. Items without an anchor keep plain surface coordinates.
- Mount ink on the Reader section, the tutor-mode question view and the card review (replacing the 12a scratch-only mounts), with a save state shown as an icon plus word: "Saved", "Saving…", "Saved on this device · will sync", "Couldn't save · Retry". Settings → Pencil & ink: pen-only drawing and predicted ink, saved to `profiles.settings.ink`.
- A section whose revision changed after the ink was drawn shows a notice ("This section was updated; your ink may be out of place"). Ink is never dropped.
## Tests
- Parity: a golden vector file of concurrent edits (both orders, ties, tombstones, clock skew) gives identical results from `mergeInkItems` (TS) and the SQL upsert (pytest against Postgres); pydantic/zod fixture parity.
- Unit: pending-map coalescing, Dexie persistence across reload, `applyRemote` keeps undo history, anchor resolution across widths, flush on pagehide.
- db: RLS (user B cannot read A's layers or items), select-only for clients, surface validation for every type, limits, tombstone GC, `reset`.
- e2e (3 browsers × 6 viewports, frozen-DOM axe): draw on a section → reload → ink is there; a second browser context sees it within one refetch; delete on one context and edit on the other → LWW result on both; offline (route abort) → draw → reload → reconnect → synced; ink beside the same paragraph at 1180 and 390 px wide within 1 px of its anchor.
## Acceptance
1. Ink on sections, questions and cards persists and syncs across devices; merge by item id with LWW and tombstones matches `mergeInkItems` exactly.
2. Nothing is lost on reload, pagehide or offline; deleted strokes never come back.
3. Section ink stays with its paragraph across reflow; matches the boards; zero axe violations / console errors.

## Decisions (lead to check)
- Items move from `ink_layers.strokes` (one jsonb blob) into per-item `ink_items` rows. This allows incremental sync, row-level LWW in SQL, and no whole-blob rewrites on a 500 MB database.
- Pydantic mirrors the zod ink schema for this one contract (the engine schema came first in 12a); fixture parity replaces generation.
- Full-tie tie-break is "stored row stays" server-side (JSON-string comparison differs between JS and Python); clients adopt the server result.
- Tombstone horizon 90 days; `updatedAt` clamp +5 min; question ink is per question (not per block); explanations carry no ink.
- PencilKit fallback (if the owner's device run fails §5.3) must write the same `InkItem` shape through this API; nothing here assumes the web engine.
