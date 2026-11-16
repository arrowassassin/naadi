# Naadi item-authoring rules (USMLE-style single-best-answer MCQs)

Version 1 (2026-11-15). Sources: NBME Item-Writing Guide, 6th ed. (form-gated PDF, full text not
read) and the NBME short-format item-writing guidelines. Also used: published evidence on AI-generated
MCQs (Law et al., BMC Med Educ 2025; Riehm et al., PLOS ONE 2026; BMC Med Educ 2025, PMC12374324),
as summarised in `research_notes/.../usmle_taxonomy_pipeline.md` sections 3–4.

This file has three parts:

- **Part A** is written so it can be pasted straight into an LLM system prompt.
- **Part B** is the linter specification. Each rule has an id, a severity and a detection method.
- **Part C** is the review workflow and the psychometric thresholds.

---

## Part A — Authoring instructions (paste into the generator system prompt)

You write original USMLE-style single-best-answer questions for Naadi. Every item must follow these rules.

**Inputs you receive:** a taxonomy cell (exam, system, discipline or clinical science, competency, topic), a
one-sentence learning objective, a difficulty target (easy/medium/hard), and source-of-truth excerpts.
Use only the provided excerpts and Naadi's own notes as factual grounding. Never reproduce or paraphrase
items from NBME, USMLE, UWorld, AMBOSS, First Aid or any other commercial or official bank.

**Structure**
1. Write a clinical (or experimental) vignette stem, then a single lead-in question, then exactly 5
   options labelled A–E. There is one key and four distractors. Use a long stem and short options.
2. Write the vignette in this order: age and sex, setting, chief complaint, duration, history of present
   illness, past medical history / medications / social / family history, vital signs, examination,
   laboratory and imaging results. Give lab values in conventional US units, with SI units in
   parentheses where they help. Values must agree with the current NBME reference ranges.
3. The lead-in must be one closed, focused question, for example "Which of the following is the most
   likely diagnosis?", "...the most appropriate next step in management?" or "...the most likely
   mechanism of action of this drug?"
4. **Cover-the-options rule:** an expert must be able to answer from the stem and lead-in alone.
5. Test application of important, common or serious concepts. Prefer higher-order items (two- or
   three-step reasoning: findings → diagnosis → mechanism, or next step) over isolated recall.
   AI-generated items have been shown to skew easy and toward lower-order thinking, so push toward the
   requested difficulty.

**Options**
6. Options must be homogeneous: all diagnoses, all tests, all drugs or all mechanisms, each rankable on
   one dimension. Do not use double options ("X and Y") unless every option is double.
7. List options in alphabetical order, or in logical or numeric order (ascending). Numeric options must
   use one consistent format and unit, with no overlapping ranges.
8. Keep options a similar length. The key must not be the longest or most specific option.
9. Never use "all of the above", "none of the above", true/false sets, or "Which of the following is
   correct/true?" stems.
10. Never use negative lead-ins (EXCEPT, NOT, LEAST).
11. Avoid absolute and vague-frequency words: always, never, only, rarely, usually, frequently, often,
    sometimes, commonly.
12. Avoid testwiseness cues:
    - grammatical cues (a/an, singular/plural agreement with the stem);
    - logical cues (an exhaustive set such as increased/decreased/unchanged);
    - word repeats (distinctive stem words reappearing only in the key);
    - convergence (the key sharing the most elements with the other options).
13. Avoid irrelevant difficulty: tricky or overly complex stems, long or complicated options,
    non-parallel wording, inconsistent numeric data.
14. Write no hinged items. An item must not depend on another item or reveal another item's answer.

**Fairness**
15. Include patient characteristics (age, sex, race/ethnicity, occupation, geography) only when they are
    clinically relevant. Never use them as stereotyped shortcuts to the answer. The NBME 6th edition
    adds guidance on this, which still needs to be read and incorporated.

**Required output fields** (JSON matching the `questions` table):
`stem_html`, `options[{label, html}]`, `answer`, `explanation_html` (why the key is right),
`why_not_html` (one sentence per distractor explaining why it is wrong), `educational_objective` (one
sentence), `difficulty`, `taxonomy_ids` (one system, plus one or more disciplines for Step 1 or clinical
sciences for Step 2 CK/3, plus one competency, plus one or more `topic.*` ids), `citations[]` (one per
factual claim, pointing to the supplied excerpts), and `nutrition: bool`.

There must be exactly one defensible best answer. If you cannot write one, say so instead of producing
an item.

---

## Part B — Automated lint spec (`tools/lint_items.py`, to be implemented)

Severity levels:

- **ERROR** blocks the `draft → auto_checked` transition.
- **WARN** sends the item to a human reviewer with a flag.
- **INFO** is logged only.

Detection runs on text with HTML stripped and converted to lower case.

| id | severity | rule | detection |
|---|---|---|---|
| S01 | ERROR | exactly 5 options, labels A–E, unique, non-empty | schema |
| S02 | ERROR | `answer` ∈ option labels | schema |
| S03 | ERROR | required fields present (explanation, why_not for all 4 distractors, objective, difficulty) | schema; why_not must mention each distractor label |
| S04 | ERROR | ≥1 system, ≥1 discipline (Step 1) / clinical_science (Step 2 CK/3), 1 competency, ≥1 topic; all ids exist in `data/taxonomy.json` | lookup |
| S05 | ERROR | `source_id` exists and its source has `allow_ai_ingest: true` for every excerpt used | `config/sources.yaml` |
| L01 | ERROR | banned option text | regex `\b(all\|none) of the above\b`, `\bboth [a-e] and [a-e]\b` |
| L02 | ERROR | negative lead-in | regex in lead-in `\b(except\|not\|least)\b` (uppercase or lowercase) |
| L03 | WARN | absolute / vague-frequency terms in stem or options | regex `\b(always\|never\|only\|rarely\|usually\|frequently\|often\|sometimes\|commonly)\b` |
| L04 | ERROR | lead-in is a single closed question ending in "?" | last sentence ends with `?`; exactly one `?` in stem; reject `which of the following (is\|are) (true\|correct)` |
| L05 | WARN | key length cue | `len(key) > 1.5 × mean(len(distractors))` (characters) |
| L06 | WARN | word-repeat cue | content-word overlap: Jaccard(stem tokens, key tokens) − max Jaccard(stem, distractor) > 0.15, ignoring stopwords and taxonomy-generic terms |
| L07 | WARN | convergence cue | key is the option with the highest mean token overlap with the other options |
| L08 | WARN | option ordering | options neither alphabetical nor monotonic numeric |
| L09 | WARN | numeric consistency | numeric options with mixed units/formats or overlapping ranges |
| L10 | WARN | homogeneity | option "type" classifier (diagnosis / drug / test / mechanism / number) disagrees across options; double options unless all double |
| L11 | WARN | grammatical cue | lead-in ends with "an" or "a" and options' initial phonemes disagree; singular/plural mismatch |
| L12 | WARN | vignette order | regex detects labs/imaging before vitals or exam |
| L13 | ERROR | lab values | every recognised analyte value parses with units and lies in a plausible range; flag if inconsistent with Naadi's own reference table (built independently, mirroring NBME units) |
| L14 | WARN | stem length | stem < 40 words (likely recall item) or > 250 words |
| L15 | WARN | hinged item | stem references "previous question" / another item id |
| L16 | ERROR | originality | max cosine similarity vs existing bank and the known-public-items index > 0.92 → reject; 0.85–0.92 → WARN |
| L17 | WARN | stereotyping | demographic term present but `demographic_relevance` rationale missing |
| L18 | INFO | difficulty target | solver confidence vs requested difficulty mismatch |

**Blind-solver checks** (run after lint passes; the solver is a different model family from the author,
with temperature 0):

- **B01 (ERROR):** the solver is given the stem, lead-in and options with no answer, and its choice must
  equal `answer`.
- **B02 (WARN):** the solver is given the stem and lead-in only (cover-the-options) and writes a free
  answer. An LLM judge then checks whether that answer matches the key. A mismatch suggests the item is
  ambiguous or depends on its options.
- **B03 (WARN):** the solver rates each distractor as "defensible as best answer" from 0 to 1. Any
  distractor above 0.5 means the item may have two answers.
- **B04 (ERROR):** citation check. Every claim in the explanation must map to a supplied excerpt, and
  uncited claims are listed. (In the cited study, AI items had a 6% factual-error rate.)

When all ERROR checks pass, the status becomes `auto_checked`. Store all flags in `questions.review.flags[]`.

---

## Part C — Review workflow and psychometrics

```
AI draft ──► automated lint (Part B) ──► blind solver (B01–B04) ──► 2 independent expert reviewers
 (draft)        ERROR → regenerate/fix       ERROR → back to draft      ──► adjudication ──► published
                                             (auto_checked)               (in_review)
                         ──► live data: psychometric screen at N ≥ 30 ──► action at N ≥ 100–200 ──► revise / retire
```

1. **Generate.** The generator receives a taxonomy cell, an objective, a difficulty target and
   AI-ingestible excerpts only. Log the model, prompt hash, excerpt ids and timestamp in
   `review.authorModel`.
2. **Lint.** Run Part B. ERRORs loop back to regeneration, up to 2 automatic retries, after which the
   item is discarded.
3. **Blind solver.** Run B01–B04. Any ERROR sends the item back to draft. WARNs are attached for the
   reviewers.
4. **Two expert reviewers.** These are two US-trained or board-relevant physicians, or one physician plus
   one trained item editor. They review independently and blind to each other. Each one rates:
   - accuracy: correct / minor issue / incorrect;
   - single best answer: yes / no;
   - cognitive level: recall / application / analysis;
   - item-writing flaws, using the Part A checklist;
   - difficulty, and whether the taxonomy tags are right.

   Both must approve. If they disagree, a third reviewer adjudicates. Record the reviewer id, date and
   decision in `review.humanReviewers[]`, and keep version history so there is an audit trail. Expect
   about 5–15% of items to be rejected or need major revision. This estimate is based on published AI
   item rates of 6% factual errors and 14% inappropriate difficulty.
5. **Publish.** The item moves to `published`. Seed items currently marked
   `ai-authored, independent-ai-reviewed` with `humanExpertReviewed: false` must go through step 4
   before they are labelled as expert-reviewed.
6. **Psychometrics.** Compute these from `attempts`, using first attempts only, timed and tutor modes
   pooled, and responses from flagged-as-guess users excluded.

| statistic | screen at N ≥ 30 (provisional flag only) | act at N ≥ 100–200 |
|---|---|---|
| p-value (proportion correct) | flag if < 0.20 or > 0.95 | target 0.30–0.85; outside → review difficulty/key |
| point-biserial (item–rest correlation) | flag if < 0.05 | ≥ 0.20 acceptable; 0.10–0.20 revise; < 0.10 or negative → re-check key, consider retiring |
| distractor choice rate | — | each distractor chosen by ≥ 5%; non-functioning distractors (< 5%) → rewrite |
| distractor point-biserial | flag if any distractor > key | every distractor should be negative; positive → possible second correct answer |
| median response time | flag if > 2× block median | review length/complexity |
| user "report error" rate | flag at ≥ 2 reports | mandatory physician review |

These thresholds are standard practice, but the research session did not find a cited source for them.
Recalibrate them once real data exist. When N is between 30 and 100, the only allowed outcome is "flag
for review": do not auto-retire an item on fewer than 100 responses. Store the results in
`questions.stats` as `{n, pCorrect, pointBiserial, optionCounts, distractorPb, computedAt}`.

7. **Revise or retire.** Any edit to the stem, options or key creates a new item version and resets the
   stats. Retired items keep their history.
