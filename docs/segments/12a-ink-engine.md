# Segment 12a — M3: Ink engine (input pipeline, rendering, tools, latency harness; no persistence)

**Implementer:** `implementer-opus`. Reason: named high-risk (ink/Pencil latency); it decides whether we need a native PencilKit wrapper.
**Reviewer:** `reviewer-opus` (mandatory).
**Wave:** A (parallel with S02, S03). Coordinate via the multiplayer ledger: agree the shell content slot and pen-surface registration with `seg03-auth`.
**Depends on:** 01 (packages/ui tokens, web scaffold, Playwright matrix).
**HANDOFF refs:** §5.1 (pointer handling), §5.3 (ink requirements and latency budget), §8 (Ink, Notebook rows). Boards: `design/Ink.dc.html`, `design/Chapter.dc.html` (drawing toolbar), `design/Notebook.dc.html` (paper styles).

## Goal
A reusable, framework-light ink engine (`packages/ink`) plus a React binding that any surface (chapter section, question, flashcard, PDF page, notebook page, exam scratch) can mount. It meets the §5.3 behaviour on iPad Safari and degrades to finger/mouse drawing elsewhere. Persistence, sync and handwriting recognition come in 12b/13; this segment exposes a clean data model and events for them.

## Scope
- **Input pipeline (Pointer Events):** draw only when `pointerType === "pen"` while a pen is present (palm rejection: ignore `touch` while a pen is down; settings toggle "pen-only drawing"). With no pen detected (phone, laptop), finger or mouse draws when the pen tool is active. `touch-action: none` only while a drawing tool is active, so one finger scrolls otherwise; two fingers always pan or zoom. Use `getCoalescedEvents()` for full-rate input and `getPredictedEvents()` (Safari 18.2+) for the live preview; feature-detect both. Work around the WebKit 18.2 coalesced-pointerId bug (validate ids) and `twist == 0` on 26.4.1 (don't rely on twist). Pencil hover only drives the cursor preview.
- **Rendering:** `perfect-freehand` 1.2.3 (pressure → width) on a `<canvas>` sized to the surface at devicePixelRatio. Two layers: committed strokes (cached bitmap, re-rendered on resize/zoom) and a live layer for the current stroke + prediction. Draw in `requestAnimationFrame`; never block the input handler.
- **Data model:** strokes `{id, tool, color, size, points: [[x, y, pressure, t], ...]}` in **surface-relative coordinates** (normalized to the surface's content box so ink survives reflow and zoom); text-anchored margin notes carry an optional ProseMirror position. Stable ids (UUIDv7) so 18's offline sync can merge by stroke id. Typed with zod in `packages/shared`.
- **Tools:** pen, marker (multiply blend), eraser (stroke and pixel/partial), lasso (select → move/copy/delete; "convert to text" and "circle to ask Naadi" emit events with a cropped PNG + bounding box for later segments), shapes (auto-straighten lines, circles, arrows on hold), text box, two ink colours from tokens (paper + `ink-violet`), undo/redo, clear with confirm.
- **Toolbar** per the Chapter/Ink boards, ≥ 44 px targets, keyboard shortcuts (P pen, M marker, E eraser, L lasso, ⌘Z/⇧⌘Z), reduced-motion aware, accessible names.
- **Events API** for consumers: `onStrokesChange(diff)`, `onLasso(selection, png)`, `onConvertToText`, `onAsk`. No network calls in this package.
- **Performance:** 60/120 fps scrolling with 5,000 strokes on a page (spatial index + cached bitmap tiles); pen-to-pixel latency measured.
- **Latency harness:** a `/system/ink-lab` page (admin/dev only, behind a feature flag) that records input→paint timestamps (`event.timeStamp` vs `requestAnimationFrame`/`performance.now()` at paint), shows p50/p95, and supports the 240 fps slow-motion video protocol in `docs/DEVICE_CHECKLIST.md`. Record the Playwright-measured numbers; the real-iPad number comes from the owner's checklist run, and the Capacitor/PencilKit decision is written to `docs/PROGRESS.md`.

## Tests
- Unit (vitest): stroke model, coordinate normalization round-trip across sizes, eraser/lasso geometry, shape recognition, undo/redo, palm-rejection state machine (pen down → touches ignored), coalesced-id validation.
- E2E (Playwright, all browsers × six viewports, axe): synthetic pen strokes (CDP/`page.mouse` + dispatched `PointerEvent` with `pointerType: "pen"` and pressure) draw; touch scroll does not draw when pen-only is on; finger draws on phone when no pen; tools and shortcuts; 5k-stroke page scroll FPS measured; ink survives resize/rotation (viewport change) without drift beyond 1 px.
- Screenshots of the ink lab and a demo surface in every state at every viewport, compared with the Ink and Chapter boards.

## Acceptance criteria
1. Pen draws, touch scrolls, palm rejection works; finger/mouse fallback where there's no pen.
2. Coalesced + predicted events used when available; strokes stored `[x, y, p, t]` surface-relative; survive reflow/zoom.
3. 5k strokes at 60 fps in Chromium/WebKit on the CI runner profile; latency harness recorded.
4. Clean events API, zero network calls, zero axe violations, zero console errors.
