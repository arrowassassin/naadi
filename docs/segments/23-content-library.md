# Segment 23: the complete Step 1 + Step 2 CK content library

**Goal.** Naadi is the only place the student needs in order to study for USMLE Step 1 and Step 2 CK. Every area of the official 2026 content outline has original, accurate, exam-level study text, original practice questions with full explanations, figures and flashcards. It also has full-length practice exams in the current format. Third-party material is linked or embedded only where its licence allows.

The research behind this brief:
- `docs/research/usmle_outline_and_text_licences.md` (the outline tree, weights, format and text-source licences);
- `docs/research/questions_media_flashcards_licences.md` (question banks, media, YouTube and flashcard decks).

Read both before starting.

## Owner decisions (2026-11-17)

- **Commercial-safe licensing.** Anything stored and shown in the app must allow commercial use: public domain, CC0, CC BY, CC BY-SA (kept in its own partition with share-alike attribution), or Naadi original. NC, ND and NC-ND sources (StatPearls, OpenStax 2e, which is now CC BY-NC-SA with an AI clause, WHO, Radiopaedia, UMich histology, Khan Academy, GeneReviews, USPSTF text) are **link-out only**. Exception: YouTube videos embed through the official player Space (S20). This keeps the option of charging later. If the owner later confirms Naadi will stay non-commercial, an NC partition can be added separately.
- **Question bank target: about 4,000 original items.** That's about 2,000 Step 1 and 2,000 Step 2 CK, with at least 6 per level-3 outline area, weighted to the exam percentages. There will be 4 full-length practice exams per Step.
- **No imported question datasets.** MedMCQA and MedQA are scraped and copyrighted, and the NBME and USMLE samples are "All rights reserved". The official free practice (usmle.org samples, NBME self-assessments) appears as clearly labelled **links**.
- **No imported Anki decks.** AnKing, Zanki and similar contain material of undetermined rights. Students may import their own decks privately (S20). Naadi generates its own cards from its own content.

## Originality rules (non-negotiable)

1. Write every item, section and card from your own medical knowledge, phrased in your own words. Never paraphrase or closely track First Aid, UWorld, AMBOSS, Boards and Beyond, Pathoma, Sketchy, NBME, StatPearls or any other copyrighted text, question, mnemonic or image.
2. Facts aren't copyrightable, but expression is. Use your own vignette details, distractor sets, explanations and structure.
3. Don't use famous commercial mnemonics verbatim. Make up original memory aids if you want them.
4. Every row cites a `sources` id. Naadi originals cite `naadi-original-v1`. Factual claims taken from a guideline (e.g. CDC immunization schedules, ADA, ACC/AHA thresholds) cite the guideline as a **reference link** (link-only source), with the recommendation restated in your own words.
5. The validator (S23-0) rejects anything that has 8-gram overlap above the threshold with the forbidden-corpus fingerprints, a missing source, or a licence the gate doesn't allow.

## Item quality rules (NBME-style)

- Single best answer, at least 5 options (A–E; more is allowed), and a clinical vignette for most items. Step 2 CK items are management-, diagnosis- and next-step-focused, and include the new formats: chart/tabular items, sequential item sets (2–3 linked items, locked once answered) and abstract items (a study summary with 2+ questions).
- Every item has:
  - `stemHtml`, `options`, `answer`;
  - `explanationHtml` (why the right answer is right, the key concept, a high-yield takeaway);
  - `whyNotHtml` for **every** wrong option;
  - an `objective`;
  - outline tags at level 3, with discipline/clinical_science/task tags;
  - `difficulty` (1–5);
  - `source`;
  - review metadata.
- Follow good item-writing practice:
  - no "all of the above" or "none of the above";
  - no negative stems unless unavoidable;
  - homogeneous options;
  - no clues from grammar or length;
  - lab values with reference ranges in the shared lab-values table;
  - SI and conventional units where the exam uses them.
- Images: original SVG diagrams (the existing figure pipeline), or CC BY/PD images (Wikimedia Commons, after checking each file's licence; PMC CC BY figures; CDC PHIL public-domain items; Servier Medical Art CC BY 4.0). Every image has full attribution, and nothing is hotlinked.
- Answers and explanations never reach the client before an attempt. This uses the existing qbank leak guards; don't add new read paths.

## Study text rules

- Each level-3 outline area gets a chapter section (or a few) at exam depth:
  - definitions, mechanism/pathophysiology, presentation, diagnosis (with the classic findings and the best initial and most accurate test), management (first-line, alternatives and when to escalate; Step 2 CK depth), complications and prognosis;
  - high-yield comparisons in tables, "exam traps" callouts, and pharmacology by mechanism (Step 1) and by clinical use and adverse effects (Step 2 CK);
  - nutrition (15–20% of both Steps), geriatrics, prescription-drug use, and the emphasis populations named in the 2026 outline.
- Use the existing chapter/section format (`data/chapters.json`, rendered by ContentRenderer) with figures through `[[figure:id]]` nodes.
- Every section ends with **Further reading** links: the matching StatPearls chapter, the guideline, and the official USMLE page where relevant. They're link-only; no text is copied.
- Each section is linked to its questions, flashcards and mapped YouTube videos (S20 mapper; only channels whose videos allow embedding).

## Tasks

### S23-0 Foundation (Opus): owner seg23-core. Do this first; the authors depend on it.

- **Taxonomy `USMLE-2026`.** It holds the 18 systems, 147 categories and 276 level-3 areas from research A.7, plus the discipline, clinical_science and task axes.
  - Store only the short area names, with a citation to the official outline. Don't store the detailed example lists verbatim.
  - Map the existing 217-node taxonomy and the 685 existing questions onto it.
  - `outline_version` is a column, so a future outline can be migrated.
- **Blueprint.**
  - Target counts per level-3 area for each Step, from the weight ranges (midpoint) × 2,000.
  - A minimum of 6 items per area per Step where that area is tested on that Step.
  - A coverage report: an admin page plus `tools/coverage.py` that fails CI if any area is under its minimum once S23 is marked complete.
- **Content packs.**
  - Split seed data into `data/content/<system>/{chapters,questions,cards,figures}.json`, one pack per author task, so authors never conflict.
  - The importer reads every pack, upserts by id and stays idempotent.
- **Validators** (`tools/validate_content.py`, run in CI):
  - schema checks;
  - every option has why-not text;
  - the answer key is consistent;
  - item-writing lint;
  - unit and lab-range checks against the lab table;
  - the licence gate, using sources.yaml tiers (above);
  - an originality check: 8-gram shingles against a fingerprint set built locally from forbidden commercial texts **that we do not store**. Only salted hashes of public snippets are committed. If no such corpus can be assembled legally, fall back to an internal near-duplicate check plus a manual review sample. Document which applies.
- **sources.yaml updates** from the research:
  - OpenStax 2e editions: NC-SA → link-only, with a note that the 1e editions are CC BY;
  - StatPearls and GeneReviews: ND → link-only;
  - USPSTF: link-only (unchanged reproduction, no profit);
  - openFDA labels: CC0, storable;
  - DailyMed: check;
  - NCI PDQ text: PD, but the images and trademark are excluded;
  - Wikipedia and WikiDoc: CC BY-SA, own partition;
  - Servier: CC BY 4.0;
  - PMC: per-article licence; only the commercial subset (CC0/BY/BY-SA) is storable;
  - Wikimedia Commons: per-file licence.
- **Practice exams.**
  - A form assembler builds 4 forms per Step that match the blueprint, at the 2026 format:
    - Step 1: 14 blocks × up to 20 items, about 280 items, 30-minute blocks;
    - Step 2 CK: 16 blocks × up to 20 items, about 316 items.
  - Items don't overlap between forms of the same Step.
  - Run them through the existing exam mode (S09) with breaks, block lock, sequential sets and the abstract format.
  - The score report is percent correct by system/discipline, plus a clearly labelled *Naadi estimate* with its uncertainty. It never claims to be an official score.
- **Official practice page.** Links to the usmle.org sample questions, the NBME self-assessments and the official Step pages, with a short note on how to use them. Links only.
- **Flashcard generation.** Cards are derived from each section's key facts and the items' learning objectives, are original, and feed FSRS (S10).

### S23-A to S23-G Authors: one cloud session each, owner seg23-<letter>. Opus, because medical accuracy is the risk.

| Task | Systems (outline ids) | Both Steps |
|---|---|---|
| S23-A | 01 Human Development, 02 Immune, 03 Blood & Lymphoreticular, plus general principles: biochemistry/genetics/cell biology, microbiology general, pharmacology principles (PK/PD, autonomics), pathology general (cell injury, inflammation, neoplasia) | yes |
| S23-B | 08 Cardiovascular, 09 Respiratory | yes |
| S23-C | 11 Renal & Urinary, 15 Endocrine, nutrition (discipline-wide) | yes |
| S23-D | 10 Gastrointestinal, 16 Multisystem (incl. infectious disease syndromes, toxicology, geriatrics) | yes |
| S23-E | 05 Nervous System & Special Senses, 04 Behavioral Health (psychiatry, psychopharmacology) | yes |
| S23-F | 06 Skin, 07 Musculoskeletal, 12 Pregnancy/Childbirth, 13 Female & 14 Male reproductive/breast (incl. gender-affirming care), pediatrics within each system | yes |
| S23-G | 17 Biostatistics/Epidemiology/Literature (incl. abstract-format sets), 18 Social Sciences (communication, ethics, law, professionalism, patient safety, systems-based practice) | yes |

Each author:
1. Claims its pack directory on the ledger, and reads the existing chapters and questions in its systems (keep, improve or extend them; never duplicate).
2. Writes study sections for **every** level-3 area in its systems, for both Steps, to the rules above.
3. Writes questions up to the blueprint targets for its areas (≥ 6 per area per Step). Spread difficulty 1–5 (about 20/30/30/15/5%), and include the image items, lab-value items and new Step 2 CK formats the outline calls for.
4. Writes flashcards for every section.
5. Adds figures (original SVG through the figure pipeline, or licensed images with attribution) wherever an image carries exam-relevant information: histology, radiology, ECGs, rashes, gross pathology, life-cycle diagrams.
6. Maps YouTube videos with the S20 mapper for each area where a good embeddable video exists (Ninja Nerd, Armando Hasudungan, MedCram and similar; check embedding is allowed).
7. Runs the validators, the importer (fresh local stack) and the coverage report, and checks every new section, item and card renders at iPad and phone.
8. Self-reviews a 10% sample against current guidelines. Integrate in batches (for example, one system at a time) so reviews can start early. Post `update` messages with counts. Post `done S23-<letter>` with the coverage numbers.

### S23-R Medical review: one reviewer session per author task (Opus)

- 100% automated validation.
- A manual expert-style review of at least 15% of items and sections, stratified by area and difficulty: factual accuracy against current guidelines, a single best answer, distractor plausibility, explanation correctness, why-not completeness, Step-appropriate depth, and originality spot-checks.
- A **zero tolerance** list:
  - a wrong answer key;
  - an unsafe management recommendation;
  - an outdated guideline;
  - copied expression;
  - an unlicensed image.
- One zero-tolerance hit in the sample triggers a full re-review of that author's batch.

### S23-Z Integration (lead): after every author is approved

- Assemble the practice forms.
- Run coverage at 100% of areas, at or above the minimums.
- Run the seed on the hosted project via the admin seed job, plus `live_check --seed` with the new counts.
- Redeploy, then run a final audit pass on the content pages at iPad, phone and laptop.

## Acceptance

- The coverage report shows every level-3 area of `USMLE-2026` at or above its minimum on each Step where it's tested. The totals are about 2,000 Step 1 and about 2,000 Step 2 CK items, plus full study text and flashcards for every area.
- There are 4 full-length forms per Step in the 2026 format, playable in exam mode, with breaks, block lock, sequential sets and abstract items working.
- Validators pass in CI, and no stored row fails the licence gate.
- Medical review approves every author batch, with zero open zero-tolerance findings.
- The official practice links page is live and the further-reading links resolve (link checker).
- Every new screen and state is checked at iPad portrait and landscape, phone 390/412 and laptop, in Playwright WebKit and Chromium. The prod CSP shows zero console errors.
