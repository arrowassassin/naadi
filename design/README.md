# USMLE Prep Hub ("Naadi") — Design source files

These files are exported verbatim from the Claude Design canvas **"USMLE Prep Hub — Website Design"**.

- Canvas link: https://claude.ai/artifact/YAgT7dYtrwxUQqz3HnXXHa
- Each `.dc.html` file is a Claude Design board source (HTML with inline CSS, plus a small `DCLogic` script that supplies sample data). Boards use design-canvas tags such as `<x-dc>`, `<helmet>`, `<sc-for>`, `<sc-if>` and `<dc-import>`, and load a `support.js` runtime that is **not** included here. Opened directly in a browser, they will not render the way they do on the canvas.
- `canvas.json` is the canvas layout manifest. It holds each board's title, size and position, the row headings and the board order.

**For developers:** use these files as a **visual reference, not production code**. Take layout, spacing, colour tokens, type scale and copy from them, then build the real components in the app's own stack. All numbers, names and `[PLACEHOLDER]` values are sample data. `System.dc.html` is the best place to start for tokens and components.

Fonts used: Bricolage Grotesque (display), Inter (body), JetBrains Mono (labels), Caveat (handwriting previews). All are loaded from Google Fonts.

## Files

| File | Size (bytes) | Board size | What it shows |
|---|---:|---|---|
| `canvas.json` | 4,043 | — | Canvas manifest: board titles, sizes, positions, row headings and order |
| `Main.dc.html` | 20,536 | 1440 × 2050 (laptop) | **Today** dashboard: "38 days." exam countdown hero, resume-lesson tile, readiness ring, today's flow, scores by system, cards due, MBBS-to-Match strip, assistant dock |
| `Lesson.dc.html` | 19,617 | 1440 × 2000 (laptop) | **Lesson**, "Acid–base, step by step": cinematic video player, five-step reading guide with highlight toolbar, AI summary, assistant side panel with flowchart and inline video |
| `Qbank.dc.html` | 12,061 | 1440 × 1700 (laptop, interactive) | **Qbank question**: DKA vignette, lab tiles, answer options with correct/incorrect states, explanation (gap, Winters, delta), Naadi coaching card |
| `Planner.dc.html` | 12,512 | 1440 × 1500 (laptop) | **Planner**, "Week 6 of 11": 7-day calendar of study blocks, road-to-exam milestones, Naadi rebalance suggestion |
| `Notes.dc.html` | 14,122 | 1440 × 1500 (laptop) | **Notes**, "Your brain, searchable": notebook list, note sheet with colour-coded highlights, AI summary, flashcards made from the note, recent highlights |
| `Tablet.dc.html` | 10,600 | 1024 × 1366 (iPad portrait) | **Lesson reader on iPad**: video player, reading step with highlight, bottom assistant sheet (high gap vs normal gap) |
| `Phone.dc.html` | 7,953 | 390 × 844 (phone) | **Assistant on phone**: chat about an uploaded PDF, correction, flowchart, inline video, quick-action chips, input bar |
| `Chapter.dc.html` | 13,429 | 1366 × 1024 (iPad landscape) | **Text-only chapter with page notes**: RTA chapter page with comparison table, side panel with typed note, Apple Pencil handwriting canvas and drawing toolbar |
| `System.dc.html` | 15,569 | 1440 × 1560 | **Naadi design system**: colour tokens, type scale, radius/spacing/touch targets, buttons, nav and assistant bar, highlights and status chips, tile variants |
| `Nav.dc.html` | 3,223 | 760 × 58 | **Shared component**, navigation pill: logo, Today/Learn/Practice/Plan/Notes/Pathway links, search, profile. `active` prop selects the current tab |
| `Dock.dc.html` | 3,025 | 780 × 66 | **Shared component**, assistant bar: attach, "Ask Naadi" input, web search, voice, send. `ask` prop sets the placeholder |
| `Onboarding.dc.html` | 7,740 | 1440 × 1300 | **Onboarding**, "When is the big day?": exam picker, date, medical school, daily hours, study days, existing resources, plan preview and diagnostic call to action |
| `Courses.dc.html` | 7,697 | 1440 × 1500 | **Course map**, "Everything, mapped.": grid of systems with progress, Renal chapter list with format and status chips |
| `Search.dc.html` | 5,543 | 1440 × 1250 | **Search everything**: large query field, filters, Naadi's short answer, grouped results (chapters, handwriting/notes, questions, web) |
| `Library.dc.html` | 6,658 | 1440 × 1400 | **Library & uploads**, "Bring your own shelf.": upload drop zone (PDF, link, camera, Anki), file-type filters, file cards |
| `Builder.dc.html` | 6,572 | 1440 × 1350 | **Qbank block builder**, "Build a block.": mode, systems, question pool and difficulty chips; block summary with question count; weak-spot block option |
| `Results.dc.html` | 8,819 | 1440 × 1650 | **Block results & analytics**: 72% score, key stats, question-by-question grid, topic bars, 10-block trend line, Naadi analysis |
| `Exam.dc.html` | 7,316 | 1440 × 1100 | **Practice exam mode**: exam header with timer, 40-question navigator, vignette with labs and options, exam tools rail (assistant silent) |
| `Cards.dc.html` | 6,549 | 1440 × 1150 (interactive) | **Flashcard review**: progress, flip card (question/answer), spaced-repetition rating buttons, scratch pad, hint panel |
| `Ink.dc.html` | 9,084 | 1024 × 1366 (iPad portrait) | **Ink on any page**: chapter page as paper with handwritten margin notes, circled and underlined text, nephron sketch, ink tool palette |
| `Notebook.dc.html` | 7,940 | 1366 × 1024 (iPad landscape) | **Notebooks**: shelf of notebook covers, open ruled page "Diuretics — where they act" with hand-drawn nephron, paper styles, ink tools |
| `Pathway.dc.html` | 7,364 | 1440 × 1650 | **MBBS to Match pathway**: 7-stage IMG route (ECFMG → Step 1 → Step 2 CK → OET/Pathway → US clinical experience → certification → ERAS/Match), official-source check, document vault, cost tracker |
| `Settings.dc.html` | 5,435 | 1440 × 1500 | **Settings**, "Make it yours.": profile, and groups for Pencil & ink, study plan, appearance, offline, assistant, data & export |
| `PhoneToday.dc.html` | 5,845 | 390 × 844 (phone) | **Today on phone**: countdown, "Now" lesson card, rest of the day, cards due and readiness, bottom tab bar |
| `PhoneChapter.dc.html` | 6,094 | 390 × 844 (phone) | **Chapter on phone**: page progress, RTA text and table, synced iPad note, page navigation bar |
| `PhoneQbank.dc.html` | 4,418 | 390 × 844 (phone) | **Question on phone**: progress and timer, vignette, labs, answer options, Flag/Labs/Submit |
| `PhonePlan.dc.html` | 5,948 | 390 × 844 (phone) | **Plan on phone**: day header, week strip, day's study blocks, Naadi rebalance prompt, tab bar |

28 source files, 245,712 bytes in total. Every file was checked against the canvas's SHA-256 checksums and matches exactly.
