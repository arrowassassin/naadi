# Naadi content guide

How content gets into Naadi legally and well: the licence catalogue, the authoring pipeline, the YouTube rules and
what "replaces books" means in numbers. The spec is HANDOFF §7; the catalogue is `config/sources.yaml`; authoring rules
are `config/authoring_rules.md`; video channels are `config/youtube_channels.yaml`.

## The one rule

**Every asset references a `sources` row.** Chapters, sections, figures, questions, cards and videos carry a
`source_id`; the row says what Naadi may do with it. The pipeline blocks publishing when an asset has no source or
licence, when non-commercial or no-derivatives content would land on a paid page, and when content not cleared for AI
ingestion would enter search embeddings or a model prompt. Never add copyrighted text (First Aid, UWorld, NBME items,
textbook passages) to the repository, the seed data, prompts or search; never ingest commercial question banks.

## Licence catalogue (`config/sources.yaml`)

| Source id | Licence | Copy | Commercial | AI/RAG | Use in Naadi |
|---|---|---|---|---|---|
| `naadi-original-v1` | Naadi proprietary | yes | yes | yes | The seed book (12 chapters, 685 questions, 95 figures) and authored content |
| `naadi-ai-generated` | Naadi proprietary | yes | yes | **no** | Generated images/clips (S15b): shown with a label, never fed back into search |
| `user-provided` | the student's own | no | no | yes (their own index only) | Uploads and pastes: private to the student, never published |
| `youtube` | embed only | no | no | no | IFrame player only; metadata refreshed or dropped within 30 days |
| `statpearls` | CC BY-NC-ND 4.0 | link | no | no | Link out only |
| `openstax` | CC BY-NC-SA 4.0, no AI ingestion | link | no | no | Link out only |
| `radiopaedia` | CC BY-NC-SA 3.0 | link | no | no | Link out only |
| `medlineplus-federal` | US public domain | yes | yes | yes | Federal pages: copy and adapt with credit |
| `medlineplus-licensed` | link only | no | no | no | Licensed parts of MedlinePlus (encyclopedia etc.): link only |
| `cdc-phil` | per item | per item | per item | no | Each image checked individually before use |
| `uspstf`, `cdc-guidelines`, `ahrq` | presumed US public domain, unverified | no until verified | no | no | Link until each document's status is verified, then record it per document |
| `wikimedia-commons` | per file | per file | per file | no | Only files whose own licence allows it, with the attribution line; SA keeps its licence |
| `usmle-official-samples`, `nbme-lab-values` | link only | no | no | no | Link out; the lab-values sheet is shown as a link, not reproduced |
| `mesh` | NLM MeSH terms | yes | yes | yes | Taxonomy terms, with attribution |
| `rxnorm` | US public domain (NLM content) | yes | yes | yes | Drug names and relations |

The table is a summary: the YAML file and the `sources` table are authoritative, and the admin licence gate
(`naadi_api/admin/licence.py` `check_asset`) is what enforces it (reasons such as `no_license`, `copy_not_allowed`,
`noncommercial_on_paid`, `share_alike_in_proprietary`, `no_ai_ingest`, `not_embeddable`, `metadata_stale`).
Adding a source: a PR that adds the row with the licence URL and the four flags, reviewed by the owner.

## Authoring pipeline (`config/authoring_rules.md`, graph `author`)

1. **Draft** (author role) from a coverage gap and allowed sources only (Naadi originals, US public domain, CC BY).
2. **Lint** against the NBME item-writing rules: single best answer, homogeneous options, no "all/none of the above",
   no absolutes, no grammatical or length cues, a lead-in answerable with the options covered.
3. **Citation check**: every claim in the explanation cites an allowed source.
4. **Blind solve** by a reviewer model from a different family; disagreement flags the item.
5. **Two human reviews** in the admin queue (`question_review`); nothing is published without them.
6. **Publish**, then psychometrics after 30 or more responses (p-value, point-biserial, distractor counts); outliers are
   flagged back into review.

AI-written items take about a quarter of the writing time but carry more factual and difficulty errors, so expert
review is mandatory (Law et al., BMC Med Educ 2025). The 685 seed questions are AI-authored and AI-reviewed: a
physician spot-check is required before any public launch (HANDOFF §13 risk 6).

## Students' own material

Uploads, links and pastes (Library) become private chapters, knowledge-base entries, figure overlays, notebook pages,
cards or private questions, only after the student confirms each placement. They are embedded with the owner's id
(searchable only by that student), carry the `user-provided` source, and never become shared content. The vault is
separate: personal documents (passport, ECFMG letters) are never indexed, cached offline or sent to a model.

## YouTube rules (YouTube API Services Developer Policies)

- Only the official IFrame player, on `youtube-nocookie.com`, inside Naadi's sandboxed player page. No downloading, no
  re-hosting, no hiding or altering the player or its ads, no nested-iframe tricks.
- Nothing loads from YouTube before the student presses play (click-to-load facade).
- Stored metadata (title, channel, duration, thumbnails) is refreshed or dropped within 30 days (`youtube_map` weekly).
- Show "YouTube" as the source with a link to YouTube's Terms; the privacy policy discloses the YouTube API use.
- Never charge to watch embedded content (III.F.3.a): video pages stay free; a lawyer reviews before any paywall.
- Quota: the mapper lists allow-listed channels' uploads (`playlistItems.list` + `videos.list`, 1 unit each), keeps
  embeddable videos, scores them against taxonomy topics offline and queues candidates; an admin approves each one,
  optionally with start/end times. `search.list` is never used. Channels are allow-listed in
  `config/youtube_channels.yaml` (verified, non-commercial-brand flags).

## Coverage goals ("replaces books", HANDOFF §7.2)

For every taxonomy topic (`data/taxonomy.json`):

| Per topic | Target |
|---|---|
| Reading | one section (original or link-out) |
| Video | one or more approved |
| Questions | 15 or more (Step 1 + CK) |
| Cards | 10 or more |

By v1.0: 3,000 Step 1 + 3,000 Step 2 CK questions, at least one section per topic, 2,000 or more cards. The admin
Coverage page shows the gaps as a heat-map by system × discipline; the nightly `author` run drafts into the largest
gaps within the job reserve of the LLM budget.

## Formats

- Sections are TipTap JSON (`chapter_sections.body`) rendered by `ContentRenderer`: paragraphs, tables, callouts
  (`pearl`, `trap`, `bridge`), figures (`media_assets` lookup), video, PDF, question embeds, Mermaid, math. An unknown
  node shows a visible error card in development and is logged in production.
- Figures are SVG, sanitised on import (`naadi_api/seed/svg.py`: no scripts, no external references, 2 MB cap), stored
  in `content-public/figures/`.
- Question stems and options are small HTML fragments rendered through an allowlist (`SafeHtml`): formatting tags and
  table spans only.
- Edits go through the admin editor with revision history; the seed importer never overwrites admin-edited sections
  unless forced (RUNBOOK "Seed content").
