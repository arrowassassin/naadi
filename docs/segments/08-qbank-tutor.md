# Segment 08 — M2: Qbank block builder, tutor mode, attempts, answer-leak protection

**Implementer:** `implementer-sonnet` (bounded CRUD + UI from patterns; leak rules are explicit). **Reviewer:** `reviewer-opus`.
**Wave:** C. **Depends on:** S02 (schema: `blocks`, `attempts` with backend-only `correct`; questions answer columns hidden from clients), S03 (auth, shell), S05 (685 seeded questions, taxonomy map, `app_config.exam_rules`), S06 (`ContentRenderer`, `questionEmbed` node).
**HANDOFF refs:** §5.1 (keyboard: 1–5/A–E answer, F flag, N next, ⌘↵ submit), §6.3 (never reveal answers before answering), §8 rows Builder, Qbank, PhoneQbank. Boards: `design/Builder.dc.html`, `design/Qbank.dc.html`, `design/PhoneQbank.dc.html`.

## Scope
### API (typed, pydantic → OpenAPI → zod)
- `GET /api/qbank/pool?exam=&systems=&disciplines=&status=unused|incorrect|flagged|all&difficulty=` → counts per filter (from attempts + taxonomy map) for the builder.
- `POST /api/blocks` `{exam, systems[], disciplines[], status, count (1–40 from exam_rules), mode: 'tutor'|'timed'|'exam', difficulty?}` → creates a block with a server-chosen, deterministic-for-seed random `question_ids` (no duplicates; respects the filters; refuses if the pool is smaller than `count` with a clear message). `POST /api/blocks/weak-spot` builds from the student's weakest taxonomy cells (accuracy × recency, min attempts) — "Or let Naadi choose".
- `GET /api/blocks/{id}` / `GET /api/blocks` (past blocks).
- `GET /api/blocks/{id}/questions/{n}` → the question **without** `answer`, `answer_html`, `explanation_html`, `why_not_html`, `educational_objective` unless the student has an attempt on it in this block (tutor mode) — served with the service role but filtered in code; tests prove every leak path is closed (block endpoint, search, flashcards, assistant page context).
- `POST /api/blocks/{id}/attempts` `{question_id, chosen, seconds, flagged}` → the **server grades** (sets `correct`), returns the explanation payload in tutor mode only; idempotent per (block, question); `PATCH` flag only.
- Lab values: `GET /api/lab-values` from `app_config` (the HANDOFF notes the NBME table is © — link out to the official PDF via `sources`; our own normal-range table is Naadi-original content seeded in app_config or a content table — do not copy NBME's).
- Question "Report an issue" uses S06's endpoint.
### Web
- **Builder** (`/practice`): mode, systems, disciplines, status and difficulty chips with live pool counts, count slider bounded by `exam_rules`, block summary, one lime CTA ("Start block"), "Build my weak-spot block", past blocks list, Flashcards due link (once S10 lands; hidden until then — no dead links).
- **Question view, tutor mode** (`/practice/blocks/$id/$n`): vignette via `ContentRenderer`, lab tiles, options A–E (keyboard 1–5/A–E, F flag, N next, ⌘↵ submit), submit → correct/incorrect states (amber + word for incorrect; never colour-only), explanation blocks ("Why it's …", per-option why-not), linked lesson chip (from taxonomy/section), "Save as flashcard" (enabled when S10 exists; hidden before), "Ask Naadi" (when the assistant flag is on; hidden before). Phone layout per PhoneQbank (Flag / Labs / Submit bar). Timed/exam modes and Results are S09 — the builder may create them, but their screens arrive in S09 (until then the builder disables those modes with a reason, not a dead link).
- Ink: the question surface mounts `InkCanvas` (S12a) as scratch — no persistence yet (12b).
- States: loading, empty pool, error, offline (an in-progress block continues from cache; attempts queue for S18's outbox — until then show "will sync when online" and retry).
## Tests
- Unit: block selection (filters, no dupes, deterministic seed), weak-spot scoring, grading, keyboard map.
- db: answer columns never returned before an attempt (all endpoints), RLS on blocks/attempts, server-side grading, idempotent attempts.
- e2e (3 browsers × 6 viewports, frozen-copy axe): build a 10-question tutor block → answer with keyboard → explanation shown only after submit → flag → next → past blocks; leak probe: request the next question's payload before answering and assert no answer fields.
## Acceptance
1. Blocks build from real seeded data with accurate pool counts; weak-spot block works.
2. No answer/explanation reaches the client before the student answers — proven across endpoints.
3. Tutor-mode flow matches Qbank/PhoneQbank boards; keyboard shortcuts work; one lime CTA; zero axe violations / console errors.
