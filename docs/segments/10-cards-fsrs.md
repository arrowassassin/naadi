# Segment 10 — M2: Flashcards with FSRS (ts-fsrs 6)

**Implementer:** `implementer-opus` (named high-risk: FSRS scheduling). **Reviewer:** `reviewer-opus`.
**Wave:** C. **Depends on:** S02 (`flashcards` with origin rules + attempt-gated question-linked cards, `card_reviews`), S03, S05 (seed has no cards — see below), S06 (`ContentRenderer`), S12a (ink scratch pad).
**HANDOFF refs:** §8 Cards row (ts-fsrs 6 defaults, optimizer-ready logs, keyboard 1–4, Anki round-trip in S20), §5.3 (scratch ink). Board: `design/Cards.dc.html`.

## Scope
### Data
- `card_reviews` holds current FSRS state per user×card; add `card_review_logs` (migration with RLS in the same file; update the RLS coverage test + live_check list): one row per rating with the full ts-fsrs `ReviewLog` fields (rating, state, due, stability, difficulty, elapsed_days, last_elapsed_days, scheduled_days, review timestamp, duration_ms) — optimizer-ready.
- Scheduling runs **on the server** with the same algorithm the client previews: use `ts-fsrs` 6.0.0 in the web for previews/offline and the official Python `fsrs` package (pin the version whose algorithm matches FSRS-6 / ts-fsrs 6 defaults; verify parity with a shared golden test vector file) on the API so a client can't forge intervals. Parameters from `app_config` (`fsrs` key: default weights, desired retention 0.9, max interval, fuzz on/off), editable by admins.
- Deck sources: user cards (`origin='user'`, created here and from S08's "Save as flashcard" and S13's highlights), assistant cards (S14), Anki import (S20). The seed has no cards; **do not invent card content**. Generate shared "Naadi core" cards only through the authoring pipeline later (S21), so this segment ships with user-created cards and the create flow.
### API
- `GET /api/cards/due?limit=` (respects the attempt gate for question-linked shared cards — service-role code must apply the same rule as RLS), `POST /api/cards` (create from text, from a question after an attempt, from a section selection), `PATCH/DELETE /api/cards/{id}` (own `origin='user'` only), `POST /api/cards/{id}/review` `{rating 1–4, duration_ms, reviewed_at}` → server computes next state, writes state + log, returns next due; idempotent on a client review id (for S18's outbox).
- `GET /api/cards/stats` (due today/tomorrow, retention, streak) for Today (S11).
### Web
- **Review** (`/cards`): progress, flip card (front/back via `ContentRenderer`), "Show answer", rating buttons Again/Hard/Good/Easy showing the next interval from the scheduler preview, keyboard Space flip + 1–4 rate, "Edit card", "Open source page" (section/question deep link), scratch ink pad (`InkCanvas`, no persistence until 12b), "Stuck?" hint actions shown only when the assistant flag is on.
- **Create/edit** dialog; empty state ("No cards due — next due …"); loading, error, offline (review continues on cached due cards; ratings queue for S18 — until then retry + "will sync").
- Builder's "Flashcards · N due" link and S08's "Save as flashcard" turn on (feature flag) once this segment lands.
## Tests
- Golden parity: a shared vector file of rating sequences → identical due/stability/difficulty from ts-fsrs (web) and the Python scheduler (API), across timezones and DST.
- db: RLS, attempt-gated cards in due lists via service-role code, idempotent reviews, logs written.
- e2e: create a card → due → flip → rate with keyboard → next due matches preview → stats; all viewports, frozen-copy axe.
## Acceptance
1. FSRS-6 defaults, server-authoritative, preview == server; optimizer-ready logs.
2. Keyboard 1–4 + Space; matches the Cards board; one lime CTA; zero axe violations / console errors.
3. Question-linked shared cards never appear before the student's attempt, through any endpoint.
