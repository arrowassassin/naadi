# Segment 06 — M1: Courses map + chapter Reader (`ContentRenderer`)

**Implementer:** `implementer-opus`. Reason: named high-risk (content renderer); every reading surface, the admin editor and the tutor's canvas reuse it.
**Reviewer:** `reviewer-opus` (mandatory).
**Wave:** B (parallel with S04, S05). Agree the TipTap node schema with `seg05-seed` first (contract); you own the React rendering of every node.
**Depends on:** S02 (schema/RLS), S03 (shell, auth), S05 (content in the DB; until it lands, run against S05's importer output in the local stack).
**HANDOFF refs:** §4 (design system), §5 (iPad), §7.5 (renderer: nothing hardcoded), §8 rows Courses and Lesson/Chapter/Tablet/PhoneChapter. Boards: `design/Courses.dc.html`, `design/Lesson.dc.html`, `design/Chapter.dc.html`, `design/Tablet.dc.html`, `design/PhoneChapter.dc.html`.

## Scope
### API
- `GET /api/courses?exam=&system=` → chapters with section counts, figure counts, question counts, **coverage** (reading/video/questions/cards per taxonomy topic — what exists today) and the user's **progress** (sections read, last position).
- `GET /api/chapters/{id}` → chapter + ordered sections (TipTap JSON body, headings, reading minutes, taxonomy ids) + resolved media for figure nodes (signed or public URLs as the bucket requires). Private user chapters only for their owner (RLS). Questions are never embedded with answers.
- `PUT /api/reading-position` (`{chapter_id, section_id, offset}`) and `GET` → syncs across devices (new `reading_positions` table via migration with RLS, or `profiles.settings` if S02's contract says so — agree with seg02 via the ledger).
- `POST /api/report-issue` (`{ref_type, ref_id, body}`) → `report_issues`.
- All responses typed (pydantic → OpenAPI → generated zod client).

### Web
- **Courses** (`/learn`): course map by system with exam filter (Step 1 / Step 2 CK) and system filter, chapter cards with coverage chips (Read · Read + watch · Questions) and progress (word + bar, never colour alone); one lime CTA ("Continue: <chapter>") per the Courses board. Mock exams appear as their own group linking to Practice once S08/S09 exist (hidden until then — no dead links).
- **Reader** (`/learn/$chapterId/$sectionId`): text-first layout per Lesson/Chapter/Tablet/PhoneChapter boards; section navigation (prev/next, contents sheet), reading position saved and restored, keyboard (←/→ sections, ⌘K reserved), progress per chapter.
- **`<ContentRenderer>`** in `packages/ui` (or `apps/web/src/content`): maps every node from the agreed schema to a component: paragraph, headings, lists, tables (scrollable on phone), marks (bold/italic/sub/sup), `highlight` (lime key fact / violet formula / amber mistake — with a word for screen readers), `callout` (pearl / trap / bridge — distinct icon + label, not colour only), `figure` (Storage image with caption, attribution from `media_assets`, zoom on tap, lazy-loaded, alt text from caption), `video` (YouTube IFrame with `origin` + `referrerpolicy`, privacy-enhanced domain, click-to-load facade), `pdf` (link/embed placeholder node handled as a real link until S15), `question-embed` (renders a question without answer — answering arrives with S08), `mermaid`, `math` (KaTeX). **Unknown node:** visible error card in dev, logged (with request id) and skipped gracefully in prod — never silently dropped.
- Selection toolbar slot (Highlight / Note / Card / Ask) reserved in the layout but **not rendered** until S13/S14 provide the actions.
- "Report an issue" on every section.
- Loading skeletons, empty (no chapters for filter), error (retry + request id), offline (cached chapter if the browser has it; banner).

## Tests
- Unit: renderer golden tests for every node type and mark; unknown-node behaviour in dev vs prod; figure URL resolution; filters.
- Integration (db): courses + chapter endpoints against seeded data; RLS for private chapters; reading position upsert; report issue.
- E2E (3 browsers × 6 viewports, axe): open course map → filter → open chapter → navigate sections → figure renders from Storage → reload restores position → second browser context sees the synced position → report an issue. Screenshots of Courses and Reader in all states.

## Acceptance criteria
1. All 14 chapters browsable from the DB; every figure renders from Storage with caption/attribution.
2. Every node type renders; unknown nodes are visible in dev and logged in prod.
3. Reading position syncs across devices; filters by exam and system work.
4. Matches the boards at every breakpoint; zero axe violations and zero console errors; one lime CTA per view.
