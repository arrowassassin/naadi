# skin-msk-repro authoring sources (S23-F)

One JSON file per USMLE-2026 leaf area: `src/<SS.CC.AA>.json`, e.g. `src/06.01.01.json`. `build.py` turns them
into the pack files (`chapters.json`, `questions.json`, `cards.json`, `figures.json`). Never edit those directly.

- `_plan.json`: per area, the number of **new** items to write per Step (`step1`, `step2ck`), from the blueprint
  target minus legacy items, with a floor of 6.
- `_links.json`: StatPearls chapter ids resolved through NCBI E-utilities (`build.py --resolve`). Generated.

Ids come from the area code and each row's `n`. Item `n` is unique within the file, and so are section,
card, figure and set `n`s, so `06.01.01` item 3 becomes `s23f-q060101-03`. **Never renumber**: append new `n`s.

```jsonc
{
  "area": "06.01.01",
  "sections": [{
    "n": 1, "exam": "step1",                  // step1 = mechanism/path/micro/pharm MOA; step2ck = diagnosis + management
    "heading": "Superficial fungal infections",
    "html": "<p>…</p>[[figure:s23f-fig060101-1]]<table class=\"hy\"><tr><th>…</th></tr></table><div class=\"trap\"><p>…</p></div>",
    "extraAreas": [],                          // optional, other leaf codes like "16.04.01"
    "furtherReading": [
      {"statpearls": "Tinea Corporis"},        // exact StatPearls chapter title; build resolves NBK id
      {"label": "CDC: STI Treatment Guidelines", "url": "https://www.cdc.gov/std/treatment-guidelines/default.htm", "sourceId": "cdc-guidelines"}
    ]
  }],
  "figures": [{"n": 1, "exam": "step1", "caption": "Figure. …"}],   // file is figures/s23f-fig060101-1.svg
  "sets": [{"n": 1, "exam": "step2ck", "kind": "sequential", "title": "…"}],
  "items": [{
    "n": 1, "exam": "step2ck", "format": "single",                 // single | chart | sequential | abstract
    "set": null, "setOrder": null,                                  // for sequential/abstract
    "stemHtml": "<p>A 34-year-old … Which of the following is the most appropriate next step in management?</p>",
    "options": ["…", "…", "…", "…", "…"],                           // 5+ html strings; labelled A.. automatically
    "answer": "C",
    "explanationHtml": "<p>Why C is right … key concept …</p><p><strong>Takeaway:</strong> …</p>",
    "whyNot": {"A": "…", "B": "…", "D": "…", "E": "…"},
    "objective": "…",
    "extraAreas": [],
    "disciplines": [],                         // required on step1 items
    "clinicalSciences": ["medicine"],          // required on step2ck items
    "tasks": ["pc_mixed_management"],
    "difficulty": 3,
    "references": ["cdc-guidelines"],          // optional link-only source ids
    "figures": [],                             // figure n's the stem shows
    "tags": ["tinea"]
  }],
  "cards": [{"n": 1, "section": 1, "front": "…?", "back": "…", "question": null}]
}
```

Commands: `python data/content/skin-msk-repro/build.py --check --only 06.01.01` (lint one area),
`--report` (counts vs plan), `--resolve` (StatPearls ids; network), no flag = write the pack files.
