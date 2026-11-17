# Question authoring, review and export-all (segment 21)

How a question gets into the bank, who decides, and where the numbers come from. Rules: `config/authoring_rules.md`
(Part A prompt, Part B linter, Part C review and psychometrics). Code: `apps/api/naadi_api/authoring/`, web:
`apps/web/src/review/`, Settings and export: `apps/web/src/settings/`, `apps/api/naadi_api/export/`.

## The pipeline (graph `author`)

```
coverage gap or admin cell ─► ground (AI-ingestible excerpts) ─► draft (author role) ─► lint (Part B) ─► citations (B04)
        ─► blind solve (reviewer role, other family: B01-B03) ─► status in_review ─► human review ─► published
```

- **Select.** Topics with fewer than `authoring.min_published_per_topic` (15) published questions (pending drafts count so a
  topic is not drafted twice), fewest first; or an admin's cell (exam, topic, discipline or clinical science, competency),
  objective and difficulty (`POST /api/admin/review/runs`, background).
- **Ground.** Excerpts only from published, shared sections whose `sources` row has `allow_ai_ingest` and a grounding
  licence (Naadi original, public domain, CC BY). Never uploads, never link-only sources. No excerpt = no draft and no model call.
- **Draft.** `author` role, purpose `job`, Part A as the system prompt, JSON out (`ItemDraft`). The taxonomy ids of the item are the
  cell's, not the model's. Logged in `questions.review.authorModel` and `authoring_runs` (model, prompt hash, excerpt ids: never
  the prompt or the excerpt text).
- **Lint.** `authoring/lint.py` (S01-S05, L01-L18), pure Python, the same function the question editor calls. An ERROR regenerates
  (`lint_retries`, 2) with the rule ids and parameters fed back, then the item is dropped. WARN/INFO travel to the reviewer.
- **Citations (B04)**, deterministic: every citation names a supplied excerpt that lexically supports its claim; the share of
  explanation sentences no excerpt supports is bounded (`uncited_max_ratio`).
- **Blind solve.** `reviewer` role at temperature 0, a different family from every author model. It sees the stem and options only.
  B01 (its choice must equal the key; ERROR, one regeneration `solver_retries`), B03 (a distractor it finds defensible, WARN),
  B02 (cover-the-options free answer judged against the key, WARN). A solver served by the author's family
  (`review_not_independent`) leaves the item `auto_checked`: it cannot reach review until an independent re-check passes.
- **Budget.** Every call goes through `naadi_api/llm.py` (free chains, rpm, daily cap, the 30 % job reserve) and a per-night call
  counter (`max_llm_calls_per_night`, 40). One draft attempt costs 4 calls (1 author + 3 solver), so the default night yields up to ten items
  only when nothing is regenerated. The nightly pass stops when the queue holds `max_pending_review` items.

## Human review

- **One admin approval publishes** (the owner is the only user) once three gates pass: no lint ERROR, the source check, and the
  model cross-check by an independent family. A reviewer may ask for a second review on one item; `required_approvals: 2` asks for
  two on every item. Two reviewers are blind to each other until both submit; if they disagree a third admin adjudicates; one admin never
  fills two slots (unique key `(question, reviewer, round)`).
- **Approve needs all Part C ratings** and cannot contradict them (inaccurate, not a single best answer, wrong taxonomy => revise or reject).
- Publish writes `review.humanReviewers[]`, a `question_revisions` snapshot (segment 19's table, once present), status `published`,
  and re-embeds. Revise sends the item back to `draft`; resubmitting it opens a new round and resets its statistics. Reject retires it.
- **Seed questions** (`humanExpertReviewed: false`) are spot-checked in the same queue (filter "Seed spot-check"); approving marks them
  human-reviewed, rejecting retires them.

## Psychometrics (`author` graph, mode `psychometrics`, 23:11 UTC)

First attempts only, tutor and timed pooled; the item-rest point-biserial uses each student's share correct on their other items.
At N >= 30 it only flags (p < 0.20 or > 0.95, point-biserial < 0.05, a distractor chosen more than the key, median time over 2x
the bank's, >= 2 open reports). Action-level flags (a distractor with a positive point-biserial, options nobody picks) need N >= 100,
and only a human can retire or revise; below 100 the only outcome is "flag for review". Results live in the hidden `questions.stats`.

## Settings and export-all

`/settings/*` is a shell over groups, each shown only while its feature flag is on. **Export everything** (`POST /api/export`) builds a
ZIP on the server: one JSON array per user table, the student's uploaded files and generated media, the vault only when
"Include Vault documents" is ticked (rows and files). It is uploaded to `user-uploads/{user_id}/exports/{id}.zip` (further parts
`-2`, `-3`, ...: the bucket caps an object at 50 MB), offered by signed URL for 24 hours, then deleted. A file larger than a part is listed
under `skipped` in `manifest.json` and stays downloadable from Library. A running exam's grading (`attempts.correct`) is withheld until the
block is finished; shared content (chapters, questions, answer keys) is never exported. Notes and notebooks as PDFs are made on the device
by the notes exporter in the same flow.

## Testing without a model

`AUTHORING_FAKE=1` (ignored when `APP_ENV=prod`) answers the pipeline's model calls from `authoring/fake.py`: a grounded, lint-clean item and a
correct, independent solver. The unit tests use scripted gateways (`tests/authoring/`); the db tests run the real HTTP path against Postgres
and the local storage-api. `tools/lint_items.py` runs the linter over a JSON file (`--seed` for the shipped bank).
