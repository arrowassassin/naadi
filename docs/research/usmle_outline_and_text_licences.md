# Research A: USMLE outline and open text sources

Fetched 2026-11-17 with `curl -sL` through the session proxy. PDFs were converted with `pdftotext`. Every quote below is copied from a fetched page or file. When a page could not be fetched, the entry says so and its status is **UNVERIFIED/unknown**. WebFetch has no DNS in this session (ENOTFOUND for every host). curl got these responses: www.cdc.gov 403 "Access Denied" (Akamai); radiopaedia.org and images.radiopaedia.org 406; geekymedics.com and statpearls.com 403; www.nih.gov 403; www.ahrq.gov timed out. NCBI Bookshelf HTML pages returned a reCAPTCHA page, so StatPearls and GeneReviews were checked instead from the NCBI Literature Archive FTP (the XML that NCBI itself distributes). Wayback-machine fallbacks were blocked by the session's permission policy.

> Not legal advice. These are findings for an engineering decision. Before launch, have counsel review the items marked as needing a decision.

---

## A. USMLE Step 1 and Step 2 CK

### A.0 Primary documents fetched

| Doc | URL | Version seen |
|---|---|---|
| Step 1 Exam Content (specs page) | https://www.usmle.org/exam-resources/step-1-materials/step-1-content-outline-and-specifications | live 2026-11-17 |
| Step 2 CK Exam Content (specs page) | https://www.usmle.org/exam-resources/step-2-ck-materials/step-2-ck-content-outline-specifications | live 2026-11-17 |
| USMLE Content Outline PDF | https://www.usmle.org/sites/default/files/2022-01/USMLE_Content_Outline_0.pdf | footer "For Public Release: USMLE Content Outline (2026)" (the 2022-01 path is stale; the content is 2026) |
| USMLE Physician Tasks/Competencies PDF | https://www.usmle.org/sites/default/files/2022-01/USMLE_Physician_Tasks_Competencies_2.pdf | "Copyright © 2025" |
| 2024 Content Outline Update infographic | https://www.usmle.org/sites/default/files/2024-01/NBME_USMLE_Infographic_final.pdf | 2024 |
| Step 1 / Step 2 CK exam pages | https://www.usmle.org/step-exams/step-1 , https://www.usmle.org/step-exams/step-2-ck | live |
| Step 1 / Step 2 CK question-format pages | https://www.usmle.org/exam-resources/step-1-materials/step-1-test-question-formats , https://www.usmle.org/exam-resources/step-2-ck-materials/step-2-ck-test-question-formats | live |
| 2027 Bulletin of Information | https://www.usmle.org/sites/default/files/2026-08/2027Bulletin.pdf | 2027 |
| Sample Step 2 CK score report | https://www.usmle.org/sites/default/files/2026-03/Sample_Step2CK_passing_score_report.pdf | test date April 26, 2026 |
| Old URL `https://www.usmle.org/pdfs/step-2-ck/Step2CK_Content.pdf` | redirects to the Step 2 CK HTML specs page (served as text/html, no PDF) | there is no separate Step 2 CK content PDF any more; the one integrated outline PDF is the source |

### A.1 Step 1 specifications (verbatim tables from the specs page)

The page says: "Test questions are classified in one of 18 major areas". It also notes "* Percentages are subject to change at any time."

**Table 1: System**
| System | Range % |
|---|---|
| Human Development*** | 1–3 |
| Blood & Lymphoreticular/Immune Systems | 9–13 |
| Behavioral Health & Nervous Systems/Special Senses | 10–14 |
| Musculoskeletal, Skin & Subcutaneous Tissue | 8–12 |
| Cardiovascular System | 7–11 |
| Respiratory & Renal/Urinary Systems | 11–15 |
| Gastrointestinal System | 6–10 |
| Reproductive & Endocrine Systems | 12–16 |
| Multisystem Processes & Disorders | 8–12 |
| Biostatistics & Epidemiology/Population Health | 4–6 |
| Social Sciences: Communication and Interpersonal Skills | 6–9 |

Footnotes (verbatim): "**Foundational Science content is distributed throughout the organ systems based on disease process/diagnosis." / "*** The Human Development topic includes Normal Age-Related Findings and Care of the Well Patient. The remaining content that had been included in the General Principles category has been distributed to other organ system categories based on disease process/diagnosis."

**Table 2: Physician Tasks/Competencies**
| Competency | Range % |
|---|---|
| Medical Knowledge: Applying Foundational Science Concepts | 60–70 |
| Patient Care: Diagnosis (History/Physical Examination; Diagnosis) | 20–25 |
| Communication and Interpersonal Skills | 6–9 |
| Practice-based Learning & Improvement | 4–6 |

**Table 3: Discipline** (the page says: "many items are classified to more than one discipline. Totals add up to greater than 100%.")
| Discipline | Range % |
|---|---|
| Pathology | 45–55 |
| Physiology | 30–40 |
| Nutrition | 15–20 |
| Gross Anatomy & Embryology | 10–20 |
| Microbiology | 10–20 |
| Pharmacology | 10–20 |
| Behavioral Sciences | 10–15 |
| Biochemistry | 5–15 |
| Histology & Cell Biology | 5–15 |
| Immunology | 5–15 |
| Genetics | 5–10 |

### A.2 Step 2 CK specifications (verbatim tables from the specs page)

The page says: "Test questions are classified into one of 18 major categories". On Nutrition it says: "Table 1 also includes the Nutrition discipline. Test items that are coded to Nutrition are also coded to one of the system content categories ... Test items coded to the Nutrition Discipline align with evidence-based competencies including but not limited to those outlined in the 2024 consensus statement Proposed Nutrition Competencies for Medical Students and Physician Trainees published in JAMA Netw Open."

**Table 1: System/Discipline**
| System/Discipline | Range % |
|---|---|
| Nutrition | 15–20 |
| Social Sciences: Legal/Ethical Issues & Professionalism/Systems-based Practice & Patient Safety | 10–15 |
| Renal & Urinary System & Reproductive Systems | 7–13 |
| Cardiovascular System | 6–12 |
| Musculoskeletal System/Skin & Subcutaneous Tissue | 6–12 |
| Behavioral Health | 5–10 |
| Blood & Lymphoreticular/Immune Systems | 5–10 |
| Gastrointestinal System | 5–10 |
| Nervous System & Special Senses | 5–10 |
| Respiratory System | 5–10 |
| Multisystem Processes & Disorders | 4–8 |
| Endocrine System | 3–7 |
| Pregnancy, Childbirth & the Puerperium | 3–7 |
| Biostatistics & Epidemiology/Population Health/Interpretation of Medical Literature | 3–5 |
| Human Development** | 2–4 |

**Table 2: Physician Tasks/Competencies**
| Competency | Range % |
|---|---|
| Patient Care: Diagnosis | 16–20 |
| Patient Care: Laboratory/Diagnostic Studies | 13–17 |
| Patient Care: Mixed Management | 12–16 |
| Patient Care: Pharmacotherapy | 8–12 |
| Patient Care: Clinical Interventions | 6–10 |
| Patient Care: Health Maintenance/Disease Prevention | 5–10 |
| Patient Care: Prognosis/Outcome | 5–9 |
| Systems-based Practice & Patient Safety | 5–7 |
| Professionalism | 5–7 |
| Practice-based Learning & Improvement | 3–5 |
| Medical Knowledge: Applying Foundational Science Concepts | 0** |
| Patient Care: History and Physical Exam | 0*** |

Footnotes (verbatim): "** Test items that assess patient care competencies may also assess knowledge of underlying foundational science concepts." / "*** Test items that assess history and physical exam competencies are covered in Step 1 and Step 3 examinations." Communication does not appear as a separate row in the Step 2 CK Table 2.

**Table 3: Clinical Science** ("Totals add up to greater than 100%.")
| Clinical Science | Range % |
|---|---|
| Medicine | 55–65 |
| Pediatrics | 17–27 |
| Obstetrics & Gynecology | 10–20 |
| Psychiatry | 10–15 |
| Surgery | 5–15 |

**How these reconcile with the 2026 sample score report.** The score report repeats Table 1 and Table 3 exactly. Its system list leaves out the Social Sciences, Biostatistics and Human Development rows. It collapses physician tasks into 4 reporting bands: "PC: Diagnosis (40 - 50%)", "PC: Management (35 - 45%)", "Ethics/Professionalism (5 - 10%)", "Systems-Based Practice/Patient Safety (5 - 10%)". These bands are aggregates of Table 2: Diagnosis + Lab/Diagnostic + Prognosis is about 34–46, and the management rows sum to about 31–48. So the two sources do not conflict. Use the specs page for item-level tagging and the score-report bands for student-facing "readiness" roll-ups.

### A.3 Physician Tasks/Competencies outline (PDF, verbatim headings)

- Medical Knowledge/Scientific Concepts
  - Applying foundational science concepts
- Patient Care: Diagnosis
  - History and physical examination · Laboratory and diagnostic studies · Diagnosis · Prognosis/outcome
- Patient Care: Management
  - Health maintenance and disease prevention · Pharmacotherapy · Clinical interventions · Mixed management
- Communication
  - Fostering the relationship · Gathering information · Providing information · Making decisions · Supporting emotions · Enabling patient behaviors · Using an interpreter or surrogate
- Professionalism, Including Legal and Ethical Issues
  - Professionalism/legal/ethical issues in interactions with patients and families (incl. those related to death and dying) · Professionalism/legal/ethical issues in interactions with other health professionals
- Systems-based Practice, Including Patient Safety
  - Systems-based practice · Patient safety
- Practice-based Learning
  - Understanding and application of principles of biostatistics and epidemiology · Application of information from studies or other written material to patient care · Understanding of the principles of research ethics and regulatory issues

The PDF then gives a task-statement list under each task: Applying Foundational Science Concepts; Diagnosis: Obtaining and Predicting History and Physical Examination; Selecting and Interpreting Laboratory and Diagnostic Studies; Formulating the Diagnosis; Determining Prognosis/Outcome; Management: Health Maintenance & Disease Prevention; Selecting and Monitoring Pharmacotherapy; Clinical Interventions/Treatment; and so on. These lists can serve as `task` tags. One foundational-science task statement is verbatim: "Identifies the nutritional underpinnings of disease processes and treatment."

### A.4 Exam format (verbatim, official)

**Step 1** (https://www.usmle.org/step-exams/step-1):
- Before May 14, 2026: "divided into seven 60-minute blocks and administered in one 8-hour testing session. The number of questions per block ... will not exceed 40. The total number of items on the overall examination will not exceed 280. ... a minimum allotment of 45 minutes of break time and a 15-minute optional tutorial."
- On or after May 14, 2026: "divided into fourteen 30-minute blocks and administered in one 8-hour testing session. The number of questions per block on a given examination form may vary, but does not exceed 20. The examination also includes a minimum allotment of 55 minutes of break time and a 5-minute optional tutorial."
- 2027 Bulletin: "Approximately 280 multiple-choice questions (MCQs), divided into fourteen 30-minute blocks", "One-day exam, approximately 8 hours".

**Step 2 CK** (https://www.usmle.org/step-exams/step-2-ck):
- Before May 7, 2026: "eight 60-minute blocks ... one 9-hour testing session ... will not exceed 40 ... will not exceed 318 ... 45 minutes of break time and a 15-minute optional tutorial."
- On or after May 7, 2026: "divided into sixteen 30-minute blocks and administered in one 9-hour testing session. The number of questions per block ... will not exceed 20. ... a minimum allotment of 55 minutes of break time and a 5-minute optional tutorial."
- 2027 Bulletin: "Approximately 316 MCQs, divided into sixteen 30-minute blocks", "One-day exam, approximately 9 hours".

**Item formats.** Step 1: single-best-answer vignettes with "four or more response options", some "interpretation of graphic or pictorial materials", and items "with associated audio findings". Step 2 CK adds the following:
- Vignette or "Chart/Tabular format".
- "Sequential Item Sets" ("two or three consecutive questions ... you will not be able to change the answer to the previous question").
- The "Abstract Format" ("a summary of an experiment or clinical investigation ... accompanied by two or more questions").
- Items "with audio or video findings".

Both Steps: "examinees can only review items within the current block and are unable to return to previous blocks once closed" (https://www.usmle.org/usmle-test-delivery-software-updates-coming-2026).

**Scoring.** The Bulletin says: "examinees typically must answer approximately 60% of questions correctly to achieve a passing score." The Step 2 CK sample report says: "Reported scores range from 1-300 with a mean of 250 and a standard deviation of 15", with a minimum passing score of 218 and "The SEE on this exam is 8 points."

### A.5 Recent changes (official sources)

| Change | Quote | URL |
|---|---|---|
| Step 1 pass/fail | "USMLE Step 1 score reporting will transition from a numeric score and pass/fail outcome to pass/fail only for exams taken on or after January 26, 2022." (Posted September 15, 2021) | https://www.usmle.org/usmle-step-1-transition-passfail-only-score-reporting |
| Step 1 pharmacology emphasis | "the primary focus of this content will be on the mechanisms of actions of drugs rather than on specific pharmacotherapy, drug-drug interactions, adverse effects, or contraindications of medications." | Step 1 formats page |
| Step 1 biostatistics, ethics | "The Step 1 examination includes items pertaining to the understanding of the principles of biostatistics and epidemiology, including principles of research ethics and regulatory issues." | Step 1 formats page |
| Communication/ethics weighting | Step 1 "Social Sciences: Communication and Interpersonal Skills 6–9%". Step 2 CK "Social Sciences: Legal/Ethical Issues & Professionalism/Systems-based Practice & Patient Safety 10-15". | specs pages |
| 2024 outline update | "Foundational science content has been recategorized into organ system categories and a new category titled 'Human Development'." / "The USMLE's 'General Principles of Foundational Science' content has been redistributed, not removed." | infographic PDF |
| Emphasis areas (2026 outline) | "there has been an emphasis on new content developed assessing competencies related to geriatric medicine, nutrition, and prescription drug use." It also names veterans/military families, "populations requiring health and gender-affirming care (eg, nonbinary and transgender patients)", and "patients with disabilities". | Content Outline PDF p.1 |
| Nutrition discipline 15–20% | Appears now in both Step 1 Table 3 and Step 2 CK Table 1; Step 2 CK cites the 2024 JAMA Netw Open nutrition competencies. **The date it took effect was not found on usmle.org: unknown.** | specs pages |
| 2026 test-delivery software | "As of May 14, 2026, these software updates are now in place for all three Step exams. ... The exam content, total number of items, and overall length of the tests remain unchanged. However, the exams have more blocks, and the blocks are shorter in length." Also new: "a settings menu and the ability to adjust the contrast on each image." (Posted May 14, 2026) | https://www.usmle.org/test-delivery-software-updates-place-step-1 |
| Practice analysis (future outline changes) | "Phase one was completed in January 2026 ... The study will ultimately inform potential updates to the USMLE content outline and task list." (Posted June 8, 2026) | https://www.usmle.org/usmle-program-conducts-practice-analysis-study |
| Designated Testing Dates | "the USMLE program has decided to implement the Designated Testing Dates exam administration model during January 2029." | https://www.usmle.org/updated-timeline-usmles-designated-testing-dates-transition-will-take-place-january-2029 |

**App implication.** The outline is about to change, because the practice analysis is in progress. Version the taxonomy (`outline_version = "USMLE-2026"`), and expect a migration when a new outline ships.

### A.6 Copyright and terms: can we store the outline text?

- Outline PDF: "Copyright © 2026 by the Federation of State Medical Boards of the United States, Inc. (FSMB), and NBME. All rights reserved." The document is also labelled "Public" / "For Public Release".
- Tasks PDF: "Copyright © 2025 by the Federation of State Medical Boards ... (FSMB), and NBME. All rights reserved."
- Site footer: "Copyright © 1996-2026 Federation of State Medical Boards (FSMB) and National Board of Medical Examiners (NBME). All rights reserved."
- Bulletin: "Examination materials are the confidential, copyrighted property of the USMLE program. If you reproduce and/or distribute any examination materials, by any means, including by memorizing and reconstructing them, you are violating the legal rights of the USMLE program." This clause covers exam items. The outline is a public study document, but the same rights holders own it.
- **Website terms of use: not found.** The footer links only Privacy, Internet-based ads, Cookies and the Consumer Health Data Privacy Policy. `/terms-of-use`, `/terms-use`, `/legal`, `/copyright` and `/terms-and-conditions` all return 404. No licence grants reuse. **Status: unknown, all rights reserved.**
- Recommendation (an engineering judgement, not a licence): store the short outline area names (levels 1–3 below) as internal taxonomy labels with a `sources` row that cites the PDF and the copyright line. These are short functional category names in a factual classification. Do not republish the full PDF text or the detailed example lists (level 4) verbatim in the UI. Paraphrase those, or link out to usmle.org. Do not use USMLE®/NBME® marks in a way that implies endorsement. For a stronger position, ask FSMB/NBME for permission.

### A.7 Machine-usable outline (topic taxonomy)

The outline was parsed from the 2026 Content Outline PDF. It has 18 systems, 147 categories (level 2) and 276 level-3 entries. Level-3 text is verbatim from the PDF. Some level-3 entries are themselves semicolon lists of example conditions; the app can split these on `;`. The deeper example lines (level 4 "o" bullets, and the "▪" / "•" sub-bullets in Social Sciences) are not reproduced; each entry shows how many it has. Numeric IDs are ours, assigned in PDF order: `SS.CC.II`.

**Mapping from Step 1/2 CK weight rows to outline systems (01–18):**

| Outline system | Step 1 row (range %) | Step 2 CK row (range %) |
|---|---|---|
| 01 Human Development | Human Development (1–3) | Human Development (2–4) |
| 02 Immune; 03 Blood & Lymphoreticular | Blood & Lymphoreticular/Immune (9–13) | same (5–10) |
| 04 Behavioral Health | Behavioral Health & Nervous Systems/Special Senses (10–14) | Behavioral Health (5–10) |
| 05 Nervous System & Special Senses | (shared row above) | Nervous System & Special Senses (5–10) |
| 06 Skin; 07 Musculoskeletal | Musculoskeletal, Skin & Subcutaneous (8–12) | same (6–12) |
| 08 Cardiovascular | 7–11 | 6–12 |
| 09 Respiratory | Respiratory & Renal/Urinary (11–15) | Respiratory (5–10) |
| 11 Renal & Urinary; 13 Female/Transgender Repro & Breast; 14 Male/Transgender Repro | Renal is in "Respiratory & Renal/Urinary"; Repro is in "Reproductive & Endocrine (12–16)" | Renal & Urinary & Reproductive (7–13) |
| 10 Gastrointestinal | 6–10 | 5–10 |
| 12 Pregnancy, Childbirth & Puerperium | in Reproductive & Endocrine (12–16) (our inference; no explicit Step 1 row) | 3–7 |
| 15 Endocrine | Reproductive & Endocrine (12–16) | 3–7 |
| 16 Multisystem | 8–12 | 4–8 |
| 17 Biostatistics/Epi/Lit | 4–6 | 3–5 |
| 18 Social Sciences | Communication & Interpersonal Skills (6–9) | Legal/Ethical/Professionalism/SBP/Patient Safety (10–15) |
| (discipline) Nutrition | Discipline 15–20 | 15–20; also outline 16.04 "Nutrition" |

Orthogonal tag axes: `discipline` (Step 1 Table 3), `clinical_science` (Step 2 CK Table 3) and `task` (A.3).


---

## B. Open text sources: licences

Legend for the columns: **Comm** = commercial use allowed; **Deriv** = adaptations allowed (summaries, flashcards, rewritten text); **Store+show** = we may store the full text in our DB and display it. The "Covers" column is our own judgement from each source's subject matter; it maps to the outline system numbers in A.7.

### B.0 Summary matrix

| Source | Licence (verified?) | Comm | Deriv | Store+show | Share-alike on our DB? | Covers (outline systems) |
|---|---|---|---|---|---|---|
| OpenStax Anatomy & Physiology **2e** (current) | CC BY-NC-SA 4.0 (verified) | No | Yes, NC + SA | Yes, non-commercial | Yes, adapted text must be CC BY-NC-SA | 01–16 normal structure and function |
| OpenStax A&P **1e** | CC BY 4.0 (verified) | Yes | Yes | Yes | No | same as above |
| OpenStax Microbiology | CC BY-NC-SA 4.0 (verified) | No | NC+SA | Yes, NC | Yes | infectious categories in 03, 05–11, 16 |
| OpenStax Biology 2e | CC BY-NC-SA 4.0 (verified) | No | NC+SA | Yes, NC | Yes | cell biology, genetics, 16.11 |
| OpenStax Chemistry 2e | CC BY-NC-SA 4.0 (verified); Chemistry (1e) CC BY 4.0 | 2e No / 1e Yes | 2e NC+SA / 1e Yes | Yes | 2e Yes | biochemistry foundations, 16.07 acid–base (low yield) |
| OpenStax Psychology 2e | CC BY-NC-SA 4.0 (verified); Psychology (1e) CC BY 4.0 | 2e No / 1e Yes | 2e NC+SA / 1e Yes | Yes | 2e Yes | 04, 01 development, 18 communication |
| LibreTexts Medicine shelves | per book: CC BY, BY-SA, BY-NC, BY-NC-SA, "not declared" (verified per book, B.2) | varies | varies | varies | varies | see B.2 |
| StatPearls (NCBI Bookshelf) | CC BY-NC-ND 4.0 in every chapter sampled (verified, 252/252) | No | **No** | Link-out only (verbatim NC redistribution is allowed, but useless for our purposes) | n/a | all clinical systems |
| WikiJournal of Medicine | CC BY by default; other "compatible OA licences can be requested" (verified) | Yes, if CC BY | Yes | Yes, record per article | Only if that article is BY-SA | scattered review articles |
| Wikipedia | CC BY-SA 4.0 (+GFDL) (verified) | Yes | Yes, SA | Yes | **Yes**, adapted text must be CC BY-SA 4.0 | all |
| WikiDoc | CC BY-SA 3.0 (footer link; verified) | Yes | Yes, SA | Yes | Yes (BY-SA 3.0; 4.0 adaptations allowed under CC compatibility, per the CC licence terms) | clinical |
| Radiopaedia | **UNVERIFIED**: fetch blocked (406) | ? | ? | ? | ? | imaging |
| Geeky Medics | **UNVERIFIED**: fetch blocked (403) | ? | ? | ? | ? | OSCE / clinical skills |
| PMC OA subset | per article; NLM groups licences into commercial / non-commercial / other (verified) | per article | per article | CC0/BY/BY-SA: yes | BY-SA articles: yes | evidence, 17 |
| NCI PDQ / NCI text | text "free of copyright"; images and PDQ trademark restricted (verified) | Yes | Yes (with conditions) | Yes | No | oncology: neoplasm categories in every system |
| MedlinePlus | mixed: PD summaries vs copyrighted A.D.A.M./ASHP (verified) | PD parts: yes | PD parts: yes | PD parts only | No | patient-level summaries |
| NLM (general) | US government works are PD; credit line requested (verified) | Yes | Yes | Yes | No | n/a |
| CDC | **UNVERIFIED**: fetch blocked (403) | ? | ? | ? | ? | infectious, vaccines, 17 |
| NIH (nih.gov) | **UNVERIFIED**: fetch blocked (403) | ? | ? | ? | ? | n/a |
| WHO | CC BY-NC-SA 3.0 IGO for WHO publications (verified) | No | NC + SA | Yes, NC | Yes | global health, infectious |
| USPSTF | AHRQ copyright notice: reproduce "without any changes"; no for-profit use (verified) | No | **No** (fair use only) | Verbatim only, with disclaimer | n/a | 01 screening, 17 |
| DailyMed SPL / FDA labels | data.gov lists ODbL; openFDA is CC0 (verified); NLM data terms apply | Yes | Yes | Yes | ODbL: share-alike on a *public derived database* | pharmacology, all "Adverse effects of drugs" categories |
| AHRQ | **UNVERIFIED**: fetch failed | ? | ? | ? | ? | 18 patient safety |
| GeneReviews | UW copyright, "noncommercial research purposes only", "No further modifications are allowed" (verified) | No | **No** | Link-out only | n/a | 16.11 genetic disorders |

### B.1 OpenStax: the licence changed in March 2026

**Finding:** OpenStax relicensed its current editions from CC BY 4.0 to CC BY-NC-SA 4.0. In the official `openstax/osbooks-*` GitHub repos, the LICENSE commit reads "updating license to CC BY NC-SA", dated 2026-03-19. For Psychology it is dated 2026-03-23. Before those commits, LICENSE was "Attribution 4.0 International".

| Book | Current licence quote | URL |
|---|---|---|
| Anatomy and Physiology 2e | "Anatomy and Physiology 2e is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license, which means that you can non-commercially distribute, remix, and build upon the content, as long as you provide attribution to OpenStax and its content contributors, and distribute all derivatives under the same license." | https://openstax.org/books/anatomy-and-physiology-2e/pages/preface |
| Microbiology | "Microbiology is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license, ..." (same wording) | https://openstax.org/books/microbiology/pages/preface |
| Biology 2e | "Biology 2e is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license, ..." | https://openstax.org/books/biology-2e/pages/preface |
| Chemistry 2e | "Chemistry 2e is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license, ..." | https://openstax.org/books/chemistry-2e/pages/preface |
| Psychology 2e | "Psychology 2e is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license, ..." | https://openstax.org/books/psychology-2e/pages/preface |
| Anatomy and Physiology (1e) | "Anatomy and Physiology is licensed under a Creative Commons Attribution 4.0 International (CC BY) license, which means that you can distribute, remix, and build upon the content, as long as you provide attribution to OpenStax and its content contributors." | https://openstax.org/books/anatomy-and-physiology/pages/preface |
| Chemistry (1e) | "Chemistry is licensed under a Creative Commons Attribution 4.0 International (CC BY) license, ..." | https://openstax.org/books/chemistry/pages/preface |
| Psychology (1e) | "Psychology is licensed under a Creative Commons Attribution 4.0 International (CC BY) license, ..." | https://openstax.org/books/psychology/pages/preface |

The OpenStax CMS API (`https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&fields=title,license_name,license_url`) agrees. It also lists Pharmacology for Nurses, Nutrition for Nurses and Medical-Surgical Nursing as CC BY-NC-SA 4.0, and Introductory Statistics (1e) and Statistics as CC BY 4.0.

**Attribution, from the A&P 2e citation tab (verbatim).**
- "If you are redistributing all or part of this book in a noncommercial digital format, then for every page that includes OpenStax content, you must license the derivative work under the same CC-BY-NC-SA license as the original, and include on every digital page view the following attribution: Access for free at https://openstax.org/books/anatomy-and-physiology-2e/pages/1-introduction".
- Trademarks: "The OpenStax name, OpenStax logo, OpenStax book covers, OpenStax CNX name, and OpenStax CNX logo, and Rice University name, and Rice University logo trademarks, or wordmarks are not subject to the Creative Commons license and may not be reproduced without the prior and express written consent of Rice University."
- Art: "Commercial entities should contact OpenStax to discuss reuse rights and permissions." / "Copyright Rice University, OpenStax, under CC BY-NC-SA 4.0 license."

**Pre-change CC BY copies.** The CC BY 4.0 legal code says: "the Licensor hereby grants You a worldwide, royalty-free, non-sublicensable, non-exclusive, irrevocable license" and "the Licensor may also offer the Licensed Material under separate terms or conditions or stop distributing the Licensed Material at any time; however, doing so will not terminate this Public License." (https://creativecommons.org/licenses/by/4.0/legalcode.en)

So the 2e content as committed *before* 2026-03-19 is arguably still usable under CC BY 4.0. For A&P 2e, that is the parent commit of `fdfa6e2` in `github.com/openstax/osbooks-anatomy-physiology`, i.e. `b0058a7` dated 2026-03-11. The relicensing commit also changed module `m46844`. LibreTexts still shows its mirror "Anatomy_and_Physiology_2e_(OpenStax)" as "shared under a CC BY 4.0" licence.

**Decision needed:** either (a) pin a pre-change commit SHA as the source and record it in `sources`, or (b) accept NC-SA for the current text. A legal reviewer should confirm option (a).

### B.2 LibreTexts (med.libretexts.org)

Terms: "LibreTexts only includes materials that are covered by open copyrights including Creative Commons, Public Domain, GNU GPL and similar. Each of these is governed by the conditions of the open copyright used which is usually found at the top of every page." / "If you download or copy any material from LibreTexts you are responsible for following all conditions consistent with the licensing of the material on your system." (https://libretexts.org/terms-conditions). The shelf index page itself is "shared under a CC BY-NC-SA 4.0 license".

Licences are tagged per page (`license:ccby`, etc.), so record the licence per page, not per book. Book-level licences, fetched from each book's landing page ("... is shared under a ... license"):

**CC BY 4.0 (best fit):**
- Anatomy and Physiology 1e (OpenStax)
- Anatomy and Physiology 2e (OpenStax): the pre-change mirror
- Human Anatomy (OERI)
- Human Anatomy Lab Manual (Stamm and Hills-Meyer)
- Human Anatomy Lab Manual (Wilk-Blaszczak)
- Neuroscience (Ju)
- Principles of Nutritional Assessment 3e (Gibson et al.)
- Public Health in Pharmacy Practice 2e (Covvey, Arya and Mager)
- Building a Medical Terminology Foundation 1e
- Medical Terminology (Grimm et al.)
- Medical Terminology 2e (OpenRN)
- Medical Terminology for Healthcare Professions (Nelson and Greene)

**CC BY-SA:**
- A Mixed Course Based Research Approach to Human Physiology (Whitmer), 4.0
- Anatomy and Physiology (Boundless), 4.0
- Computational Cognitive Neuroscience 3e, 3.0
- Drugs Quality Control: The Coursebook, 4.0

**CC BY-NC (no SA, no ND):**
- Human Reproduction: A Clinical Approach (Bastawros), 4.0
- Foundations of Epidemiology (Bovbjerg), 4.0
- Open Neuroscience Initiative (Lim), 4.0
- Culinary Medicine 2025e, 4.0
- Atlas of Otolaryngology Head and Neck Operative Surgery, 3.0

**CC BY-NC-SA:**
- Cell Biology, Genetics and Biochemistry for Pre-Clinical Students, 4.0
- Principles of Pharmacology (Rosow, Standaert and Strichartz), 4.0
- Acid-base Physiology (Brandis), 2.0
- Fluid Physiology (Brandis), 2.0
- Textbook of Cardiology, 3.0
- Principles of Epidemiology (Antunez), 4.0
- Exploring the U.S. Healthcare System, 4.0
- Value-based Healthcare (Khalil), 4.0
- Urogynaecology, 2.5
- Human Anatomy (Lange et al.), 4.0
- Human Anatomy Laboratory Manual, 4.0
- Foundations of Neuroscience (Henley), 4.0
- Human Nutrition 2020e (Hawaii), 4.0
- most other Nutrition and Public Health titles
- Breathe Easy, 4.0
- The Language of Medical Terminology, 4.0
- The Science of Nutrition (Lehman), 4.0
- Human Anatomy and Physiology Preparatory Course (Liachovitzky), version not stated

**"not declared" (treat as all rights reserved: link-out only):**
- Animal Anatomy
- Human Anatomy (no author)
- Medical Spanish and Cultural Competency for Health Care
- Introduction to Public Health (Morales)

Coverage, our judgement:
- Basic science (biochemistry, genetics) maps to 16.07/16.11 and the foundational content in all systems.
- Pharmacology maps to the "Adverse effects of drugs" categories.
- Epidemiology maps to 17; health systems maps to 18.
- Reproduction maps to 12–14; cardiology to 08; acid–base and fluid to 16.07.

### B.3 StatPearls (NCBI Bookshelf NBK430685)

The NCBI web pages served a reCAPTCHA. The check used the NCBI Literature Archive package `https://ftp.ncbi.nlm.nih.gov/pub/litarch/3d/12/statpearls_NBK430685.tar.gz` (≈2.0 GB, updated 2026-11-15). The first 40 MB contained 252 chapter XML files. **All 252** carry `xlink:href="https://creativecommons.org/licenses/by-nc-nd/4.0/"` with this text:

> "Copyright © 2023, StatPearls Publishing LLC This book is distributed under the terms of the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0) ( http://creativecommons.org/licenses/by-nc-nd/4.0/ ), which permits others to distribute the work, provided that the article is not altered or used commercially. You are not required to obtain permission to distribute this article, provided that you credit the author and journal."

No CC BY 4.0 chapter was seen in the sample. The full ~2 GB set was not scanned, so a CC BY chapter elsewhere in the set remains **unknown**. Before any ingest, parse `<license>` per chapter.

StatPearls' own terms page (statpearls.com/Account/ShowTerms) was **not fetchable (DNS/403): unverified.**

→ ND: **link-out only**. No summaries, no flashcards, no RAG chunks shown as derived text. Whether RAG retrieval of ND text for *internal* grounding is permitted is unknown and needs legal review; the safest course is to exclude it.

### B.4 WikiJournal of Medicine

"All WikiJournal of Medicine publications are open access. They are by default published under a Creative Commons Attribution License (CC BY), however other compatible OA licences can be requested by authors. ... The licensing of a work must be displayed among the details on the same online page and PDF document." / "authors retain copyright (but allow sharing and reuse as per the license)." (https://en.wikiversity.org/wiki/WikiJournal_of_Medicine/Ethics_statement)

The wiki page footer says: "Text is available under the Creative Commons Attribution-ShareAlike License". The hosting wiki's own text is therefore BY-SA. Record the per-article licence from the article's details box.

### B.5 Wikipedia

- "Permission is granted to copy, distribute and/or modify Wikipedia's text under the terms of the Creative Commons Attribution-ShareAlike 4.0 International License and, unless otherwise noted, the GNU Free Documentation License" (https://en.wikipedia.org/wiki/Wikipedia:Copyrights)
- "Wikipedia content can be copied, modified, and redistributed if and only if the copied version is made available on the same terms to others and acknowledgment of the authors of the Wikipedia article used is included (a link back to the article is generally thought to satisfy the attribution requirement...)" (same page)
- WMF Terms of Use: "When modifying or making additions to text that you have obtained from a Project Website, you agree to license the modified or added content under CC BY-SA 4.0 or later". Attribution may be given "Through hyperlink (where possible) or URL to the page or pages that you are reusing". Reusers must include "a licensing notice stating which license the work is released under, along with either a hyperlink or URL to the text of the license". (https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)

**Share-alike implications for our DB.** CC BY-SA 4.0 §4 says: "if You include all or a substantial portion of the database contents in a database in which You have Sui Generis Database Rights, then the database in which You have Sui Generis Database Rights (but not its individual contents) is Adapted Material" (https://creativecommons.org/licenses/by-sa/4.0/legalcode.en).

In practice:
- Store Wikipedia-derived rows in their own table or partition, with a licence column of CC BY-SA 4.0.
- Any rewritten summaries or cards made from them must be released under CC BY-SA 4.0.
- Do not mix them into NC-licensed derivatives (CC BY-SA and BY-NC-SA are not compatible).
- Images on Commons carry per-file licences.

### B.6 WikiDoc

Footer: "Content is available under Creative Commons Attribution/Share-Alike License unless otherwise noted." The licence link is `http://creativecommons.org/licenses/by-sa/3.0/` (https://www.wikidoc.org/index.php/Main_Page). WikiDoc's own guidance also refers to "the Creative Commons Attribution-ShareAlike (CC-BY-SA) 3.0 license" (https://wikidoc.org/index.php/How_to_avoid_plagiarism). Version: **3.0**. WikiDoc:Copyrights returned 404.

### B.7 Radiopaedia, Geeky Medics: UNVERIFIED

radiopaedia.org and images.radiopaedia.org returned HTTP 406; geekymedics.com returned 403. No licence quote could be captured. The notes in research_urls.md ("modified CC BY-NC-SA 3.0", "text belongs to Radiopaedia") are secondary and unconfirmed. **Status: unknown → link-out only** until verified by a human in a browser.

### B.8 PubMed Central OA subset

- "License terms vary. Please refer to the license statement in each article for specific terms of use." (https://pmc.ncbi.nlm.nih.gov/tools/openftlist/)
- Licence categories (https://pmc.ncbi.nlm.nih.gov/tools/textmining/):
  - "Commercial use allowed: CC0, CC BY, CC BY-SA, CC BY-ND"
  - "Non-commercial use only: CC BY-NC, CC BY-NC-SA, CC BY-NC-ND"
  - "Other: no machine-readable Creative Commons license, no license tagged, or a custom license"
- Retrieval: "The PMC Cloud Service, PMC OAI-PMH Service, E-Utilities and BioC API are the only services that may be used for automated retrieval of PMC content. Systematic retrieval (or bulk retrieval) of articles through any other automated process is prohibited." The legacy oa_comm/oa_noncomm FTP directories were removed in August 2026, and licence information is now in per-article JSON metadata (https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/).
- NLM data terms require users to "acknowledge NLM as the source of the data in a clear and conspicuous manner" and "NOT to indicate or imply that NLM/NIH/HHS has endorsed its products". Users must also "maintain the most current version of all distributed data, or make known ... that the products ... do not reflect the most current/accurate data" (https://ftp.ncbi.nlm.nih.gov/pub/pmc/readme.txt).
- Use only CC0, CC BY and CC BY-SA for adapted text. CC BY-ND articles are commercial but ND, so verbatim or link-out only.

### B.9 US government: NCI/PDQ, MedlinePlus, NLM, CDC, NIH, AHRQ, USPSTF

**NCI** (https://www.cancer.gov/policies/copyright-reuse):
- "Unless otherwise indicated, all text within National Cancer Institute (NCI) products is free of copyright and may be reused without our permission. Credit the National Cancer Institute as the source."
- "Graphics created and owned by private-sector designers will be credited to individuals or companies. Permission to reuse these copyrighted graphics must be negotiated directly with the creators".
- "The NCI logo and the PDQ trademark may only be used in reproductions of original, wholly unchanged NCI products."

**PDQ summaries** (https://www.cancer.gov/types/breast/hp/breast-treatment-pdq, "Permission to Use This Summary"):
- "PDQ is a registered trademark. Although the content of PDQ documents can be used freely as text, it cannot be identified as an NCI PDQ cancer information summary unless it is presented in its entirety and is regularly updated."
- "Images in this summary are used with permission ... for use within the PDQ summaries only."

→ The text can be stored and adapted. Do not call adapted text "PDQ". Exclude PDQ images.

**MedlinePlus** (https://medlineplus.gov/about/using/usingcontent/):
- "Some of the content on MedlinePlus is in the public domain (not copyrighted), and other content is copyrighted and licensed specifically for use on MedlinePlus."
- The public-domain areas are listed as: "Summaries on health topic pages", "Medical test information", "Summaries on Genetics pages ...", "Illustrations with the 'U.S. National Library of Medicine' watermark and credit", "Healthy recipes", "MedlinePlus videos", "Tutorials".
- The copyrighted areas are listed as: "Any content from the A.D.A.M. Medical Encyclopedia" and "Drug monographs from the American Society of Health-System Pharmacists". "Images on Health Topic and Genetics pages with no attribution: These are stock images licensed expressly for MedlinePlus; you may not reproduce or redistribute these images."
- Credit line: "Courtesy of MedlinePlus from the National Library of Medicine".

**NLM** (https://www.nlm.nih.gov/web_policies.html): "Works produced by the U.S. government are not subject to copyright protection in the United States. Any such works found on National Library of Medicine (NLM) Web sites may be freely used or reproduced without permission in the U.S. Please acknowledge NLM as the source ..." The phrase "in the U.S." matters: PD status outside the US is **unknown**.

**CDC, NIH (nih.gov), AHRQ policy page:** **UNVERIFIED** (403 / 403 / no response). The summary of CDC terms in research_urls.md (public domain unless noted; cite CDC; no endorsement; logo and third-party images excluded) is unconfirmed. As a provisional rule, CDC-authored text is presumably a US government work under the NLM principle above, but CDC's own statement could not be captured.

**USPSTF** (https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/copyright-notice):
- "AHRQ permits members of the public to reproduce, redistribute, publicly display, and incorporate USPSTF work into other materials provided that it is reproduced without any changes to the work of portions thereof, except as permitted as fair use under the U.S. Copyright Act."
- "Any adaptations of these electronic documents and resources must include a disclaimer to this effect." That disclaimer says "AHRQ and the U.S. Department of Health and Human Services cannot endorse, or appear to endorse, derivative or excerpted materials".
- "This work may not be reproduced, reprinted, or redistributed for a fee, nor may the work be sold for profit or incorporated into a profit-making venture without the express written permission of AHRQ." / "When parts of a recommendation statement are used or quoted, the USPSTF Web page should be cited as the source."

→ The notice is effectively ND and NC. Show verbatim quotes with a citation and the disclaimer, or link out. Teach the underlying facts (screening ages and intervals) in our own words; facts are not copyrightable, but this is a legal-review item.

### B.10 FDA labels: DailyMed and openFDA

- The data.gov DailyMed entry lists `license: http://opendefinition.org/licenses/odc-odbl/`, publisher "National Library of Medicine" (https://catalog.data.gov/dataset/dailymed). The DailyMed pages themselves link only to NLM's general copyright page; no DailyMed-specific licence statement was found.
- NLM download terms (https://www.nlm.nih.gov/databases/download/terms_and_conditions.html): "acknowledge NLM as the source of the data by including the phrase 'Courtesy of the U.S. National Library of Medicine' in a clear and conspicuous manner", "not indicate or imply that NLM has endorsed its products/services/applications", and maintain currency or disclose that the data are not current.
- openFDA (https://open.fda.gov/license/): "unless otherwise noted, the content, data, documentation, code, and related materials on openFDA is public domain and made available with a Creative Commons CC0 1.0 Universal dedication. ... You can copy, modify, distribute and perform the work, even for commercial purposes". The openFDA drug label endpoint therefore looks like the cleanest route (CC0) to the same SPL label content.
- Whether manufacturer-authored label text carries any residual copyright is **unknown**. No statement was found either way.

### B.11 WHO

- "WHO's open access applies to: all publications published by WHO CC BY-NC-SA 3.0 IGO" (https://www.who.int/about/policies/publishing/copyright)
- "The CC BY-NC-SA 3.0 IGO licence allows users to freely copy, reproduce, reprint, distribute, translate and adapt the work for non-commercial purposes, provided WHO is acknowledged as the source using the following suggested citation: [Title]. [Place of publication]: World Health Organization; [Year]. Licence: CC BY-NC-SA 3.0 IGO."
- Required adaptation disclaimer: "This is an adaptation of an original work '[Title]. Geneva: World Health Organization (WHO); [Year]. Licence: CC BY-NC-SA 3.0 IGO'. This adaptation was not created by WHO. WHO is not responsible for the content or accuracy of this adaptation. The original edition shall be the binding and authentic edition".
- "WHO logo cannot be used without written authorization from WHO." and "WHO publications cannot be used to promote or endorse products, services or any specific organization."
- The claim that pre-2017 items are not CC was **not found** on these pages: unknown. Check the licence line in each publication.

### B.12 GeneReviews (NBK1116)

From the NCBI Literature Archive XML (`ca/84/gene_NBK1116.tar.gz`; 20 of 20 sampled chapters are identical):

> "Copyright © 1993-2026, University of Washington, Seattle. GeneReviews is a registered trademark of the University of Washington, Seattle. All rights reserved. GeneReviews® chapters are owned by the University of Washington. Permission is hereby granted to reproduce, distribute, and translate copies of content materials for noncommercial research purposes only, provided that (i) credit for source ( https://www.genereviews.org ) and copyright (© 1993-2026 University of Washington) are included with each copy; (ii) a link to the original material is provided whenever the material is published elsewhere on the Web; and (iii) reproducers, distributors, and/or translators comply with the GeneReviews® Copyright Notice and Usage Disclaimer . No further modifications are allowed."

The notice points to https://www.ncbi.nlm.nih.gov/books/NBK138602/, which was blocked by reCAPTCHA and is **unverified**. "Noncommercial research purposes" may not cover a study app: **unknown → link-out only**.

---

## C. Recommendations for the `sources` table

1. Add the columns `licence_spdx` (e.g. `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC-BY-NC-SA-4.0`, `CC-BY-NC-ND-4.0`, `LicenseRef-USGov-PD`, `LicenseRef-AHRQ-USPSTF`, `LicenseRef-GeneReviews`, `LicenseRef-unknown`), `licence_quote`, `licence_url`, `retrieved_at`, `source_commit_sha` (for OpenStax/GitHub pins), `adaptable bool`, `share_alike_group`.
2. Default policy:
   - Adapt and store: CC0, CC BY, US-government PD (with credit lines), openFDA.
   - Adapt with partitioning: BY-SA (Wikipedia, WikiDoc).
   - NC-SA (current OpenStax 2e, WHO, many LibreTexts titles): acceptable while the app is non-commercial, but it locks those derivatives to NC-SA and blocks any future commercial pivot.
   - Link-out only: ND, all-rights-reserved and unknown (StatPearls, GeneReviews, USPSTF adaptations, Radiopaedia, Geeky Medics, "not declared" LibreTexts).
3. Re-check per item at ingest. Both OpenStax and PMC changed licensing or distribution in 2026.
4. Hosts to allow, or for a human to verify in a browser: www.cdc.gov, www.nih.gov, www.ahrq.gov, radiopaedia.org, images.radiopaedia.org, geekymedics.com, www.statpearls.com, www.ncbi.nlm.nih.gov (reCAPTCHA).

---

## A.7 (continued): Outline tree, USMLE Content Outline 2026

Source: https://www.usmle.org/sites/default/files/2022-01/USMLE_Content_Outline_0.pdf, "Copyright © 2026 by the Federation of State Medical Boards of the United States, Inc. (FSMB), and NBME. All rights reserved." It is reproduced here for internal engineering reference; see A.6 before shipping it in the UI.

- `01` **Human Development**
  - `01.01` Normal age-related findings and care of the well patient
    - `01.01.01` Infancy and childhood (0–12 years) _(+3 example lines in PDF)_
    - `01.01.02` Adolescence (13–17 years) _(+3 example lines in PDF)_
    - `01.01.03` Adulthood (18–64 years) _(+3 example lines in PDF)_
    - `01.01.04` Older Adulthood (65 years and older) _(+3 example lines in PDF)_
- `02` **Immune System**
  - `02.01` Disorders associated with immunodeficiency
    - `02.01.01` deficiency primarily of humoral immunity _(+1 example line in PDF)_
    - `02.01.02` deficiency/dysfunction primarily of cell-mediated immunity _(+1 example line in PDF)_
    - `02.01.03` complement deficiency _(+1 example line in PDF)_
    - `02.01.04` deficiency of phagocytic cells and natural killer cells _(+1 example line in PDF)_
  - `02.02` HIV/AIDS
    - `02.02.01` HIV1 and HIV2; AIDS; AIDS complications (eg, neuropathy, dementia, renal insufficiency); immunology of AIDS; immune reconstitution syndrome (IRS); secondary infections; noninfectious complications (eg, Kaposi sarcoma)
  - `02.03` Immunologically mediated disorders
    - `02.03.01` hypersensitivity reactions _(+1 example line in PDF)_
    - `02.03.02` transplantation _(+1 example line in PDF)_
  - `02.04` Adverse effects of drugs on the immune system
    - `02.04.01` Jarisch-Herxheimer reaction; drugs affecting the immune system (eg, prednisone, azathioprine, cyclosporine, methotrexate, monoclonal antibody drugs [eg, abciximab, adalimumab; bevacizumab, infliximab, omalizumab, rituximab]); vaccine adverse effects
- `03` **Blood & Lymphoreticular System**
  - `03.01` Infectious and immunologic, including microbiologic and host response to insult
    - `03.01.01` infectious disorders _(+3 example lines in PDF)_
    - `03.01.02` primary infections of lymphoid tissue _(+1 example line in PDF)_
    - `03.01.03` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `03.02` Neoplasms
    - `03.02.01` leukemia, acute (ALL, AML); leukemia, chronic (CLL, CML); lymphomas, Hodgkin disease, non-Hodgkin lymphoma, Burkitt lymphoma, T-cell lymphoma; multiple myeloma, dysproteinemias, monoclonal gammopathy of unknown significance (MGUS); myelofibrosis; myelodysplastic syndrome, myelodysplasias; other immunoproliferative neoplasms (eg, Waldenstrom macroglobulinemia)
  - `03.03` Anemia, cytopenias, and polycythemia anemias
    - `03.03.01` decreased production _(+1 example line in PDF)_
    - `03.03.02` hemolysis _(+1 example line in PDF)_
    - `03.03.03` disorders of hemoglobin, heme, or membrane _(+1 example line in PDF)_
    - `03.03.04` other causes of anemia _(+1 example line in PDF)_
    - `03.03.05` cytopenias _(+1 example line in PDF)_
    - `03.03.06` cythemias _(+1 example line in PDF)_
  - `03.04` Coagulation disorders (hypocoagulable and hypercoagulable conditions)
    - `03.04.01` hypocoagulable _(+1 example line in PDF)_
    - `03.04.02` hypercoagulable _(+1 example line in PDF)_
    - `03.04.03` reactions to blood components _(+1 example line in PDF)_
  - `03.05` Traumatic, mechanical, and vascular disorders
    - `03.05.01` mechanical injury to erythrocytes (eg, cardiac valve hemolysis)
    - `03.05.02` disorders of the spleen _(+1 example line in PDF)_
  - `03.06` Adverse effects of drugs on the hematologic and lymphoreticular systems
    - `03.06.01` antiplatelet drugs; antithrombin drugs (eg, dabigatran); chemotherapeutic agents; inhibitors of coagulation factors; methemoglobinemia, acquired; propylthiouracil; tumor lysis syndrome; warfarin
- `04` **Behavioral Health**
  - `04.01` Psychotic disorders
    - `04.01.01` brief psychotic disorder; delusional disorder; psychotic disorder due to another medical condition; schizophrenia; schizoaffective disorder; substance-induced psychotic disorder
  - `04.02` Anxiety disorders
    - `04.02.01` acute stress disorder; anxiety disorders (generalized anxiety disorder, anxiety due to another medical condition, social phobia); hyperventilation syndrome; obsessive-compulsive disorder; panic disorder with and without agoraphobia; phobic disorders; post-traumatic stress disorder; selective mutism; separation anxiety disorder; substance-induced anxiety disorder, trichotillomania
  - `04.03` Mood disorders
    - `04.03.01` major depressive disorder with and without psychotic features, with and without seasonal pattern; major depressive disorder, postpartum, with and without psychotic features, including screening; cyclothymic disorder; persistent depressive disorder; bipolar disorder, manic/depressed/mixed; premenstrual syndrome, premenstrual tension syndrome, premenstrual dysphoric disorder; bipolar and related disorder or depressive disorder due to another medical condition; substance/medication-induced bipolar and related disorder or depressive disorder (illegal or prescribed); suicidal ideation/attempt
  - `04.04` Somatic symptoms and related disorders
    - `04.04.01` body dysmorphic disorder; functional neurologic symptom disorder (conversion disorder), including psychogenic seizures; dissociative disorders; illness anxiety disorder (hypochondriasis); malingering; pain disorder; somatic symptom disorder
  - `04.05` Factitious disorders
    - `04.05.01` factitious disorder imposed on self
  - `04.06` Eating disorders and impulse-control disorders
    - `04.06.01` anorexia nervosa; binge-eating disorder; bulimia nervosa; eating disorder; disruptive, impulse-control, and conduct disorders (eg, gambling, kleptomania, pyromania)
  - `04.07` Disorders originating in infancy/childhood
    - `04.07.01` reactive attachment disorder; attention-deficit/hyperactivity disorder; speech sound disorder or language disorder; learning disorder/dyslexia; intellectual developmental disorder and developmental delay, undefined, including school problems, fetal alcohol syndrome; oppositional defiant disorder, conduct disorder; autism spectrum disorder, Rett syndrome; psychoses with origin specific to childhood; elimination disorders (incontinence, encopresis); tic disorders/Tourette disorder
  - `04.08` Personality disorders
    - `04.08.01` antisocial personality disorder; avoidant personality disorder; borderline personality disorder; dependent personality disorder; histrionic personality disorder; narcissistic personality disorder; obsessive-compulsive personality disorder; paranoid personality disorder; schizoid personality disorder
  - `04.09` Psychosocial disorders/behaviors
    - `04.09.01` adjustment disorder; grief response/bereavement, normal and persistent complex; parent-child relational problems other than physical or emotional abuse; other psychosocial stress; psychodynamic and behavioral factors, related past experience (eg, transference, personality traits)
  - `04.10` Sexual disorders and gender dysphoria
    - `04.10.01` gender dysphoria; psychosexual dysfunction
  - `04.11` Substance use disorders
    - `04.11.01` alcohol use disorder/intoxication/dependence/withdrawal; tobacco/nicotine use disorder/dependence/withdrawal; varenicline use; cannabis use disorder/intoxication/dependence; hallucinogen use disorder/intoxication/dependence/withdrawal; inhalant use disorder/intoxication/dependence/withdrawal; opioid, heroin, including prescription drug, use disorder/intoxication/dependence/withdrawal; sedative, hypnotic, including benzodiazepine and barbiturate use disorder/intoxication/dependence/withdrawal; stimulant, cocaine, methamphetamine use disorder/intoxication/dependence/withdrawal; other drugs of use disorders (eg, ecstasy, PCP, bath salts)/intoxication/dependence/withdrawal; polysubstance use disorder/intoxication/dependence/withdrawal
- `05` **Nervous System & Special Senses**
  - `05.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `05.01.01` infectious disorders _(+1 example line in PDF)_
    - `05.01.02` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `05.02` Neoplasms (cerebral, spinal, and peripheral)
    - `05.02.01` benign (meningioma, neurofibromatosis)
    - `05.02.02` malignant (glioblastoma, astrocytoma, medulloblastoma, primary CNS lymphoma)
    - `05.02.03` metastatic (eg, breast, lung, pancreatic, testicular, melanoma)
  - `05.03` Cerebrovascular disease
    - `05.03.01` arteriovenous malformations, ectatic cerebral vessels; transient ischemic attack; stroke, thrombotic: cerebral artery occlusion/cerebral infarction; stroke, embolic: cerebral embolism; stroke: intracerebral hemorrhage, including subarachnoid hemorrhage, traumatic intracranial hemorrhage; cerebral artery aneurysm; carotid artery stenosis/atherosclerosis/occlusion/dissection; vertebral artery deficiency/dissection; subclavian steal syndrome; vascular dementia; hypertensive encephalopathy; posterior reversible encephalopathy syndrome; venous sinus thrombosis
  - `05.04` Disorders relating to the spine, spinal cord, and spinal nerve roots
    - `05.04.01` cauda equina syndrome; spinal artery thrombosis/embolus/infarct; spinal cord compression; spinal cord transection, paraplegia and quadriplegia, acute and chronic effects (eg, autonomic dysreflexia); spinal stenosis (cervical, lumbar); syringomyelia
  - `05.05` Cranial and peripheral nerve disorders
    - `05.05.01` cranial nerve injury/disorders _(+1 example line in PDF)_
    - `05.05.02` peripheral nerve/plexus injury/disorders _(+1 example line in PDF)_
  - `05.06` Neurologic pain syndromes
    - `05.06.01` complex regional pain syndrome (reflex sympathetic dystrophy, causalgia); fibromyalgia; postherpetic neuralgia; phantom limb pain/syndrome; thalamic pain syndrome; trigeminal neuralgia
  - `05.07` Degenerative disorders/amnestic syndromes
    - `05.07.01` Alzheimer disease; frontotemporal dementia, including progressive supranuclear palsy, Lewy body disease; mild neurocognitive disorder, mild cognitive impairment
  - `05.08` Global cerebral dysfunction
    - `05.08.01` altered states of consciousness; delirium; coma/brain death
  - `05.09` Neuromuscular disorders
    - `05.09.01` amyotrophic lateral sclerosis/spinal muscular atrophy; muscular dystrophy (eg, Duchenne, myotonic); muscle channelopathies (eg, hypokalemic periodic paralysis)
  - `05.10` Movement disorders
    - `05.10.01` acute dystonia; adult tic disease; essential tremor; Huntington disease; Parkinson disease, including Parkinson dementia
  - `05.11` Metabolic disorders
    - `05.11.01` adrenoleukodystrophy; metabolic encephalopathy
  - `05.12` Paroxysmal disorders
    - `05.12.01` headache, including migraine, mixed, tension, ice-pick, cluster, medication withdrawal, caffeine withdrawal; seizure disorders, including generalized tonic-clonic, partial, absence, febrile
  - `05.13` Sleep disorders
    - `05.13.01` cataplexy and narcolepsy; circadian rhythm sleep-wake disorder; insomnia, primary; sleep terror disorder and sleepwalking; REM sleep behavior disorder; restless legs syndrome
  - `05.14` Traumatic and mechanical disorders and disorders of increased intracranial pressure
    - `05.14.01` anoxic brain damage, cerebral hypoxia; epidural, subdural hematoma (cerebral and spinal); intraparenchymal hemorrhage, traumatic subarachnoid hemorrhage; cerebral edema; idiopathic intracranial hypertension; torticollis/cervical dystonia; hydrocephalus, including normal-pressure; traumatic brain injury (concussion)/postconcussion syndrome (dementia pugilistica); traumatic brain syndrome
  - `05.15` Congenital disorders
    - `05.15.01` Friedreich ataxia; neural tube defects (eg, spina bifida, holoprosencephaly, anencephaly); microcephaly; Sturge-Weber syndrome; tuberous sclerosis, von Hippel-Lindau disease; hydrocephalus, obstructive (Arnold-Chiari)
  - `05.16` Adverse effects of drugs on the nervous system
    - `05.16.01` acute dystonic reaction; drug-induced meningitis (eg, NSAIDs, sulfa drugs); drug-induced neuropathy (eg, vincristine, isoniazid, metronidazole); extrapyramidal adverse effects (eg, akathisia, dystonia, drug-induced parkinsonism); neuroleptic malignant syndrome; poisoning by psychotropic agents, including antidepressants; serotonin syndrome; tardive dyskinesia
  - `05.17` Disorders of the eye and eyelid
    - `05.17.01` infectious and inflammatory disorders of the eye, including microbiologic and host response to insult _(+1 example line in PDF)_
    - `05.17.02` neoplasms of the eye _(+1 example line in PDF)_
    - `05.17.03` disorders of the eye and eyelid, structural _(+1 example line in PDF)_
    - `05.17.04` disorders of the pupil, iris, muscles (extraocular) _(+1 example line in PDF)_
    - `05.17.05` disorders of the retina _(+1 example line in PDF)_
    - `05.17.06` traumatic and mechanical disorders _(+1 example line in PDF)_
    - `05.17.07` adverse effects of drugs on the eyes _(+1 example line in PDF)_
  - `05.18` Disorders of the ear
    - `05.18.01` infectious and inflammatory disorders of the ear, including microbiologic and host response to insult _(+1 example line in PDF)_
    - `05.18.02` neoplasms _(+1 example line in PDF)_
    - `05.18.03` hearing loss/deafness _(+1 example line in PDF)_
    - `05.18.04` disorders of balance and spatial orientation _(+1 example line in PDF)_
    - `05.18.05` traumatic and mechanical disorders _(+1 example line in PDF)_
    - `05.18.06` adverse effects of drugs on the ear _(+1 example line in PDF)_
- `06` **Skin & Subcutaneous Tissue**
  - `06.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `06.01.01` infectious disorders and infestations _(+10 example lines in PDF)_
    - `06.01.02` immunologic and inflammatory disorders _(+8 example lines in PDF)_
  - `06.02` Neoplasms
    - `06.02.01` benign neoplasms, cysts and other skin lesions _(+1 example line in PDF)_
    - `06.02.02` malignant neoplasms _(+1 example line in PDF)_
  - `06.03` Integumentary disorders (hair and hair follicles, nails, sweat glands, sebaceous glands, oral mucous membranes)
    - `06.03.01` disorders of the hair and hair follicles _(+1 example line in PDF)_
    - `06.03.02` disorders of the nails (including ingrowing nail)
    - `06.03.03` disorders of sweat and sebaceous glands _(+1 example line in PDF)_
  - `06.04` Oral disease
    - `06.04.01` aphthous ulcers (stomatitis, canker sores); leukoplakia
  - `06.05` Disorders of pigmentation
    - `06.05.01` albinism; lentigo
  - `06.06` Traumatic and mechanical disorders
    - `06.06.01` animal bites (dogs, cats, etc); burns or wounds affecting the skin or subcutaneous tissue (eg, sunburn, other, including blast injuries and burns); cauliflower ear; effects of ultraviolet light; keloids; tattoo; thermal injury; perniosis; frostbite; ulcers, decubitus
  - `06.07` Congenital disorders
    - `06.07.01` xeroderma pigmentosum; benign lesions in neonates, infants, children (eg, congenital nevi)
  - `06.08` Adverse effects of drugs on skin and subcutaneous tissue
    - `06.08.01` drug reactions, eruptions, including local reaction to vaccine
- `07` **Musculoskeletal System**
  - `07.01` Infectious, inflammatory, and immunologic disorders, including microbiologic and host response to insult
    - `07.01.01` infectious disorders _(+1 example line in PDF)_
    - `07.01.02` immunologic disorders _(+1 example line in PDF)_
    - `07.01.03` inflammatory disorders _(+1 example line in PDF)_
  - `07.02` Neoplasms
    - `07.02.01` benign neoplasms (eg, osteoid osteoma, enchondroma, tenosynovial giant cell tumor)
    - `07.02.02` malignant neoplasms of bone (eg, osteosarcoma, sarcoma, leiomyosarcoma, rhabdomyosarcoma); metastases to bone, secondary malignant neoplasm of bone
  - `07.03` Degenerative and metabolic disorders
    - `07.03.01` degenerative/metabolic disorders of bone, tendon, and cartilage _(+1 example line in PDF)_
    - `07.03.02` degenerative/metabolic disorders of joints _(+1 example line in PDF)_
    - `07.03.03` degenerative/metabolic disorders of muscles, ligaments, fascia _(+1 example line in PDF)_
  - `07.04` Traumatic and mechanical disorders
    - `07.04.01` amputation and care of amputees; backache, including low back pain; blast injuries; compartment syndrome; contractures, hospital-acquired; contusions; dislocations; fractures; sprains, strains; kyphoscoliosis, scoliosis; rotator cuff syndrome; slipped capital femoral epiphysis; dislocation of hip
  - `07.05` Congenital disorders
    - `07.05.01` achondroplasia/dwarfism; disorders of limb development (HOX gene mutation, phocomelia); developmental dysplasia of the hip; dislocation of hip in infantile spinal muscular atrophy; genu valgum or varum; foot deformities (flat foot, valgus/varus deformities); osteogenesis imperfecta; McArdle disease; mitochondrial myopathies
  - `07.06` Adverse effects of drugs on the musculoskeletal system
    - `07.06.01` drug-induced myopathy (eg, steroids, statins, cocaine, AZT)
    - `07.06.02` malignant hyperthermia
- `08` **Cardiovascular System**
  - `08.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `08.01.01` infectious disorders _(+1 example line in PDF)_
    - `08.01.02` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `08.02` Neoplasms
    - `08.02.01` myxoma, metastases
  - `08.03` Dysrhythmias
    - `08.03.01` premature beats (PACs, PVCs); atrial flutter/fibrillation; multifocal atrial tachycardia; paroxysmal tachycardias; ventricular tachycardia/fibrillation; wide complex tachycardia; torsades de pointes; bradycardias; atrioventricular block (first-, second-, third-degree); conduction disorder (LBBB, RBBB); cardiac arrest; sick sinus syndrome; prolonged QT syndrome; Wolff-Parkinson-White syndrome; carotid sinus hypersensitivity; pacemaker dysfunction, including failure to sense, capture
  - `08.04` Heart failure
    - `08.04.01` chordae tendineae rupture; congestive heart failure; cor pulmonale; diastolic dysfunction; systolic dysfunction; mitral valve dysfunction; heart failure secondary to myocardial infarction; high-output heart failure, including thyrotoxicosis-induced, anemia-induced; tachycardia-induced; cardiogenic pulmonary edema
  - `08.05` Ischemic heart disease
    - `08.05.01` acute coronary syndrome, acute myocardial infarction; angina pectoris, stable and unstable/coronary artery disease/coronary insufficiency; coronary artery spasm
  - `08.06` Diseases of the myocardium
    - `08.06.01` cardiomyopathy, dilated, including alcoholic, viral, takotsubo; cardiomyopathy, obstructive hypertrophic; cardiomyopathy, familial dilated; cardiomyopathy, restrictive; hypertensive heart disease, left ventricular hypertrophy, right ventricular hypertrophy; complications of myocardial infarction; nontraumatic tamponade post-myocardial infarction; papillary muscle rupture/dysfunction; ventricular free wall rupture; myocarditis
  - `08.07` Diseases of the pericardium
    - `08.07.01` chronic constrictive pericarditis; pericardial effusion; pericardial tamponade; acute pericarditis; pericarditis, following myocardial infarction, surgery, trauma
  - `08.08` Valvular heart disease
    - `08.08.01` valve disorders, mitral/aortic/tricuspid, pulmonic (eg, regurgitation, stenosis, prolapse, insufficiency, vegetation); functional murmurs; rheumatic heart disease; complications of artificial valves
  - `08.09` Hypotension
    - `08.09.01` orthostatic hypotension
  - `08.10` Hypertension
    - `08.10.01` elevated blood pressure reading without diagnosis of hypertension; essential hypertension; hypertensive emergency; secondary hypertension
  - `08.11` Dyslipidemia
    - `08.11.01` hypercholesterolemia; hyperlipidemia; hypertriglyceridemia; lipoproteins/lipoprotein lipase deficiency
  - `08.12` Vascular disorders
    - `08.12.01` disorders of the great vessels _(+1 example line in PDF)_
    - `08.12.02` peripheral arterial vascular disease _(+1 example line in PDF)_
    - `08.12.03` diseases of the veins _(+1 example line in PDF)_
  - `08.13` Traumatic and mechanical disorders
    - `08.13.01` ventricular puncture; myocardial contusion; myocardial rupture; traumatic aortic dissection; traumatic tamponade
  - `08.14` Congenital disorders, including disease in adults
    - `08.14.01` anomalous left coronary artery; atrial septal defect; coarctation of the aorta; endocardial cushion defect; patent foramen ovale; patent ductus arteriosus; tetralogy of Fallot; transposition of the great vessels; ventricular septal defect
  - `08.15` Adverse effects of drugs on the cardiovascular system
    - `08.15.01` adriamycin; cocaine, amphetamine, PCP; ACE inhibitors, calcium channel blockers, alpha blockers, minoxidil
- `09` **Respiratory System**
  - `09.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `09.01.01` infectious, immunologic, and inflammatory disorders of the upper airways _(+1 example line in PDF)_
    - `09.01.02` infectious, immunologic, and inflammatory disorders of the lower airways _(+1 example line in PDF)_
  - `09.02` Neoplasms
    - `09.02.01` benign neoplasms: upper airways (eg, vocal cord polyps, nasal polyps, juvenile papillomatosis); lungs and pleura (eg, solitary pulmonary nodule)
    - `09.02.02` malignant neoplasms _(+2 example lines in PDF)_
    - `09.02.03` metastatic neoplasms including pleural
  - `09.03` Obstructive airway disease
    - `09.03.01` asthma, reactive airway disease; bronchiectasis; chronic airway obstruction; chronic obstructive pulmonary disease (COPD), chronic bronchitis, emphysema
  - `09.04` Pneumoconiosis/fibrosing/restrictive pulmonary disorders/interstitial lung disease
    - `09.04.01` pneumoconiosis; asbestosis; silicosis; silo-filler's lung, byssinosis, bagassosis, berylliosis; hypersensitivity pneumonitis; hypereosinophilic syndromes, Loeffler syndrome; interstitial pneumonia, usual (UIP), desquamative (DIP), nonspecific
  - `09.05` Respiratory failure/respiratory arrest and pulmonary vascular disorders
    - `09.05.01` acute respiratory distress syndrome (ARDS); pulmonary hypertension; pulmonary vascular disorders, arteriovenous fistula; pulmonary edema, pulmonary cause and unspecified; pulmonary embolism; air and fat embolism; respiratory failure due to enteral feeding
  - `09.06` Metabolic, regulatory, and structural disorders
    - `09.06.01` disorders of gas exchange; hypoventilation; hypoxia; ventilation-perfusion imbalance
  - `09.07` Disorders of the pleura, mediastinum, and chest wall
    - `09.07.01` chylothorax; costochondritis; empyema; hemothorax; mediastinitis; pleural effusion; pleuritis; pneumomediastinum; pneumothorax
  - `09.08` Traumatic and mechanical disorders
    - `09.08.01` upper airways _(+1 example line in PDF)_
    - `09.08.02` lower airways and pleura _(+1 example line in PDF)_
  - `09.09` Congenital disorders
    - `09.09.01` bronchogenic cysts; congenital cysts; congenital diaphragmatic hernia; pulmonary sequestration; immotile cilia syndrome
  - `09.10` Adverse effects of drugs on the respiratory system
    - `09.10.01` bleomycin, amiodarone; adverse effects of 100% oxygen; acute effects of tobacco/nicotine, inhalants, cocaine
- `10` **Gastrointestinal System**
  - `10.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `10.01.01` infectious disorders _(+8 example lines in PDF)_
    - `10.01.02` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `10.02` Neoplasms
    - `10.02.01` benign neoplasms, including polyps, cysts _(+1 example line in PDF)_
    - `10.02.02` malignant neoplasms and pre-malignant conditions _(+1 example line in PDF)_
    - `10.02.03` metastatic neoplasms
  - `10.03` Signs, symptoms, and ill-defined disorders
    - `10.03.01` abdominal pain, constipation, diarrhea, nausea, and vomiting—not otherwise specified [NOS]
  - `10.04` Disorders of the oral cavity, salivary glands, and esophagus
    - `10.04.01` oral cavity and salivary glands _(+1 example line in PDF)_
    - `10.04.02` esophagus _(+1 example line in PDF)_
  - `10.05` Disorders of the stomach, small intestine, colon, rectum, anus
    - `10.05.01` stomach _(+1 example line in PDF)_
    - `10.05.02` small intestine, colon _(+1 example line in PDF)_
    - `10.05.03` rectum and anus _(+1 example line in PDF)_
  - `10.06` Disorders of the liver and biliary system, noninfectious
    - `10.06.01` liver _(+1 example line in PDF)_
    - `10.06.02` biliary system _(+1 example line in PDF)_
  - `10.07` Disorders of the pancreas
    - `10.07.01` pancreatitis, acute; pancreatitis, chronic; pancreatitis, hereditary; pancreatic cyst/pseudocyst; pancreatic duct obstruction; pancreatic insufficiency
  - `10.08` Disorders of the peritoneal cavity
    - `10.08.01` ascites
  - `10.09` Traumatic and mechanical disorders
    - `10.09.01` abdominal wall defects; adhesions, postsurgical; digestive system complications of surgery; post-gastric surgery syndromes (eg, blind loop syndrome, adhesions); duodenal tear; foreign body in digestive system; inguinal, femoral, and abdominal wall hernias; open wound, abdominal; perforation of hollow viscus and blunt trauma; perforation/rupture of esophagus (Boerhaave syndrome); umbilical hernia
  - `10.10` Congenital disorders
    - `10.10.01` annular pancreas, biliary atresia, cleft lip and palate, esophageal atresia, malrotation without volvulus, Meckel diverticulum, pyloric stenosis, tracheoesophageal fistula
  - `10.11` Adverse effects of drugs on the gastrointestinal system
    - `10.11.01` drug-induced changes in motility (chronic laxative use, opioids); drug-induced gastritis, duodenitis, peptic ulcer disease (NSAIDs); drug-induced hepatitis (eg, acetaminophen, isoniazid); drug-induced pancreatitis (eg, thiazide diuretics)
- `11` **Renal & Urinary System**
  - `11.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `11.01.01` infectious disorders _(+4 example lines in PDF)_
    - `11.01.02` immunologic and inflammatory disorders _(+5 example lines in PDF)_
  - `11.02` Neoplasms
    - `11.02.01` benign neoplasms and cysts _(+1 example line in PDF)_
    - `11.02.02` malignant neoplasms _(+1 example line in PDF)_
  - `11.03` Abnormalities of urine/urination
    - `11.03.01` dysuria, hematuria, oliguria, anuria, proteinuria—not otherwise specified [NOS]
  - `11.04` Metabolic and regulatory disorders
    - `11.04.01` acute kidney injury; renal insufficiency; azotemia, uremic syndrome; chronic kidney disease, including end-stage renal disease; cystinuria; Fanconi syndrome; hypertensive renal disease (renal complications of hypertension); renal calculi, ureteral calculi, nephrolithiasis; renal tubular acidosis
  - `11.05` Vascular disorders
    - `11.05.01` renal artery stenosis (atherosclerosis, fibromuscular dysplasia, nephrosclerosis); renal vein thrombosis; renal infarction
  - `11.06` Traumatic and mechanical disorders
    - `11.06.01` bladder rupture; neurogenic bladder; obstructive uropathy; posterior urethral valves; renal laceration; renal vascular injury; ureteral laceration/avulsion/disruption; urethral diverticulum; obstruction/stricture/prolapse, urethral/ureteral, vaginal walls, uterine, uterovaginal; urinary incontinence, including secondary enuresis; vesicoureteral reflux
  - `11.07` Congenital disorders
    - `11.07.01` double ureters/ureteral duplication/double collecting system; horseshoe kidney; hydronephrosis/reflux; renal agenesis, renal hypoplasia, renal dysplasia; single kidney
  - `11.08` Adverse effects of drugs on the renal and urinary system
    - `11.08.01` ACE inhibitors; aminoglycosides; amphotericin B; cisplatin; furosemide; gadolinium (nephrogenic systemic fibrosis); heroin; iodinated contrast dye; lithium; NSAIDs; penicillins; sulfa drugs; tenofovir; drug-induced urinary retention
- `12` **Pregnancy, Childbirth, & the Puerperium**
  - `12.01` Prenatal care
    - `12.01.01` preconception counseling and care _(+1 example line in PDF)_
    - `12.01.02` prenatal risk assessment/prevention _(+1 example line in PDF)_
    - `12.01.03` supervision of normal pregnancy _(+1 example line in PDF)_
  - `12.02` Obstetric complications
    - `12.02.01` abortion, induced, septic, missed, spontaneous, threatened; acute fatty liver of pregnancy; anemia of pregnancy, sickle cell disease, thalassemia in pregnancy; antepartum hemorrhage, including third-trimester bleeding; cardiomyopathy of pregnancy; cervical insufficiency, cervical shortening; cholestasis of pregnancy, intrahepatic; congenital abnormalities, maternal (eg, bicornuate uterus); ectopic pregnancy; fetal abnormality affecting management of mother (eg, hydrocephalus, spina bifida); fetal growth restriction; gestational diabetes; maternal mortality; multiple gestation; placental abnormalities (abruptio placentae, placenta previa, premature separation of placenta); polyhydramnios, oligohydramnios; preeclampsia, eclampsia, HELLP syndrome, gestational hypertension; prolonged pregnancy; Rh isoimmunization affecting management of mother; vomiting in pregnancy (morning sickness, hyperemesis gravidarum); trauma in pregnancy; infections complicating pregnancy
  - `12.03` Labor and delivery
    - `12.03.01` labor and delivery, uncomplicated; labor and delivery, complicated, including shoulder dystocia; cesarean delivery, including complications; cord compression, cord prolapse; fetal malpresentations (eg, breech/external cephalic); intrapartum fetal evaluation, including fetal heart tones; intrapartum prophylaxis (eg, HIV, Chlamydia, gonococcal prophylaxis); premature rupture of membranes; preterm (before 37 weeks' gestation) and postdates labor and delivery; threatened preterm labor
  - `12.04` Puerperium, including complications
    - `12.04.01` lactation problems; breast-feeding problems; lochia; postpartum cardiomyopathy; postpartum blues; postpartum hemorrhage; postpartum sepsis; retained placenta, products of conception (eg, placenta accreta); uterine atony
  - `12.05` Newborn (birth to 4 weeks of age)
    - `12.05.01` normal newborn _(+1 example line in PDF)_
    - `12.05.02` disorders of the newborn _(+1 example line in PDF)_
  - `12.06` Adverse effects of drugs on pregnancy, childbirth, and the puerperium
    - `12.06.01` alcohol, tobacco, and other drugs (ATOD); prenatal radiation exposure; teratology (eg, ACE inhibitors, SSRIs, warfarin, infections, toxins)
  - `12.07` Systemic disorders affecting pregnancy, labor and delivery, and puerperium
    - `12.07.01` appendicitis; asthma; carpal tunnel syndrome in pregnancy; cirrhosis; deep venous thrombosis (DVT); diabetes mellitus; heart failure, valvular heart disease; hypertension; myasthenia gravis; obesity; pancreatitis; psychiatric disorders; renal calculus/calculi; renal failure/renal disease, including SLE; seizure disorders; thyroid disorders, hypothyroidism, hyperthyroidism
- `13` **Female and Transgender Reproductive System & Breast**
  - `13.01` Breast
    - `13.01.01` infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult _(+1 example line in PDF)_
    - `13.01.02` neoplasms _(+4 example lines in PDF)_
  - `13.02` Female reproductive system
    - `13.02.01` infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult _(+1 example line in PDF)_
    - `13.02.02` Neoplasms of the vulva, vagina, cervix, uterus, or ovaries _(+2 example lines in PDF)_
    - `13.02.03` Fertility and infertility _(+1 example line in PDF)_
    - `13.02.04` Menopause _(+1 example line in PDF)_
    - `13.02.05` Menstrual and endocrine disorders _(+1 example line in PDF)_
    - `13.02.06` Sexual dysfunction _(+1 example line in PDF)_
    - `13.02.07` Traumatic and mechanical disorders _(+1 example line in PDF)_
    - `13.02.08` Congenital disorders _(+1 example line in PDF)_
    - `13.02.09` Adverse effects of drugs on the female reproductive system and breast _(+1 example line in PDF)_
- `14` **Male and Transgender Reproductive System**
  - `14.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `14.01.01` infectious disorders _(+1 example line in PDF)_
    - `14.01.02` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `14.02` Neoplasms
    - `14.02.01` malignant neoplasms, penile, prostate, testicular, breast
  - `14.03` Metabolic and regulatory disorders, including sexual dysfunction
    - `14.03.01` erectile dysfunction, impotence; infertility, male factor; male sexual dysfunction; premature ejaculation; priapism
  - `14.04` Traumatic and mechanical disorders
    - `14.04.01` benign prostatic hyperplasia/hypertrophy; circumcision, including complications; epididymal cyst; hydrocele (including neonatal hydrocele); injuries, wounds, and burns to male genitalia, including blast injuries; penile laceration, penile fracture, Peyronie disease; phimosis; scrotal laceration; spermatocele; testicular rupture/avulsion/laceration; torsion of testis; urethral laceration/disruption; varicocele
  - `14.05` Congenital disorders
    - `14.05.01` hypospadias; Klinefelter syndrome; undescended testicle
  - `14.06` Adverse effects of drugs on the male reproductive system
    - `14.06.01` alcohol; androgens, testosterone; antipsychotics, antidepressants including selective serotonin reuptake inhibitors; beta-adrenergic blockers; diuretic-including thiazides; finasteride, dutasteride; sildenafil, tadalafil, vardenafil; marijuana; nitric oxide reductase inhibitors
- `15` **Endocrine System**
  - `15.01` Diabetes mellitus and other disorders of the endocrine pancreas
    - `15.01.01` diabetes mellitus _(+1 example line in PDF)_
    - `15.01.02` hypoglycemia and islet cell disorders _(+1 example line in PDF)_
  - `15.02` Thyroid disorders
    - `15.02.01` cyst, nodule; nonthyroidal illness syndrome (euthyroid sick syndrome); goiter (euthyroid-normal thyroid function with goiter); hypothyroidism; hyperthyroidism, including thyrotoxicosis and thyroid storm; thyroiditis, including Hashimoto; Graves disease; neoplasms (benign cysts and nodules, thyroid cancer including papillary, follicular, medullary, and anaplastic); thyroid deficiency from pituitary disorder; infertility due to thyroid disease; secondary hypothyroidism and hyperthyroidism
  - `15.03` Parathyroid disorders
    - `15.03.01` hyperparathyroidism; hypoparathyroidism; metabolic bone disease
  - `15.04` Adrenal disorders
    - `15.04.01` corticoadrenal insufficiency (Addison disease); adrenal insufficiency, secondary; hypocortisolism; Cushing syndrome; hyperaldosteronism; neoplasms, benign and malignant (adrenal neuroblastoma, pheochromocytoma, adrenal carcinoma, adrenal adenoma, aldosteronoma, adrenal incidentaloma); delayed and precocious puberty; hypertensive endocrine disease
  - `15.05` Pituitary disorders
    - `15.05.01` acromegaly/gigantism; diabetes insipidus; galactorrhea not associated with childbirth; panhypopituitarism from any cause; pituitary apoplexy (eg, Sheehan syndrome); growth hormone deficiency; short stature; SIADH (inappropriate secretion of ADH [vasopressin]); neoplasm, benign and malignant (pituitary adenomas, craniopharyngioma, metastatic disease); prolactinoma and hyperprolactinemia, including infertility due to these disorders; hypogonadism, primary and secondary
  - `15.06` Hypothalamic endocrine disorders
  - `15.07` Multiple endocrine neoplasia (MEN1, MEN2)
  - `15.08` Congenital disorders
    - `15.08.01` disorders of sexual differentiation; congenital adrenal hyperplasia; androgen insensitivity/resistance syndrome; congenital hypothyroidism
  - `15.09` Adverse effects of drugs on the endocrine system
    - `15.09.01` drug, medicinal, and biologic substance effects; exogenous steroid suppression of adrenal glands, anabolic steroids
- `16` **Multisystem Processes & Disorders**
  - `16.01` Infectious, immunologic, and inflammatory disorders, including microbiologic and host response to insult
    - `16.01.01` infectious disorders _(+8 example lines in PDF)_
    - `16.01.02` immunologic and inflammatory disorders _(+1 example line in PDF)_
  - `16.02` Neoplasms and related disorders
    - `16.02.01` paraneoplastic syndromes _(+1 example line in PDF)_
    - `16.02.02` inherited cancer syndromes _(+1 example line in PDF)_
  - `16.03` Signs, symptoms, and ill-defined disorders
    - `16.03.01` not otherwise specified [NOS] arthralgias/joint pain; chest pain; cough; dizziness, light-headedness; dyspnea, shortness of breath; fatigue; fever of unknown origin; frailty; failure to thrive; hemoptysis; lymphedema; pain management (in a nonaddiction, nonpalliative-care setting, including appropriate opioid-prescribing practices); palpitations; pruritus; syncope, including breath-holding spells with syncope; unexpected weight gain/weight loss
  - `16.04` Nutrition
    - `16.04.01` protein-calorie malnutrition (kwashiorkor, marasmus)
    - `16.04.02` vitamin deficiencies and/or toxicities _(+1 example line in PDF)_
    - `16.04.03` mineral deficiencies and/or toxicities
    - `16.04.04` obesity
    - `16.04.05` enteral/parenteral nutrition (TPN)
  - `16.05` Toxins and environmental extremes
    - `16.05.01` physical and associated disorders _(+4 example lines in PDF)_
    - `16.05.02` chemical including Gulf War illness _(+5 example lines in PDF)_
  - `16.06` Venomous bites and stings
    - `16.06.01` hymenoptera bites and stings; scorpion bites; snake bites; spider bites; jellyfish stings
  - `16.07` Fluid, electrolyte, and acid-base balance disorders
    - `16.07.01` fluid volume and electrolyte/ion disorders: fluid volume disorders; dehydration; hypovolemia; volume overload; electrolyte disorders; hyponatremia, hypernatremia; hypokalemia, hyperkalemia; hypocalcemia, hypercalcemia; hypophosphatemia, hyperphosphatemia; hypomagnesemia
    - `16.07.02` acid-base disorders: metabolic acidosis; metabolic alkalosis; respiratory acidosis; respiratory alkalosis; mixed acid-base disturbances
  - `16.08` Abuse
    - `16.08.01` child, nonaccidental trauma/inflicted head trauma/factitious disorder by proxy
    - `16.08.02` intimate partner abuse, sexual, emotional, and physical including injuries related to abuse (eg, rib fractures)
    - `16.08.03` elder abuse, sexual, emotional, and physical including injuries related to abuse (eg, rib fractures)
    - `16.08.04` sexual assault
  - `16.09` Multiple trauma
    - `16.09.01` prioritization, blast injury involving more than one organ system
  - `16.10` Shock
    - `16.10.01` shock, cardiogenic, hypovolemic, neurogenic, septic, sepsis, bacteremia, systemic inflammatory response syndrome (SIRS), refractory, multiorgan dysfunction syndrome: meningococcemia
  - `16.11` Genetic, metabolic, and developmental disorders
    - `16.11.01` multifactorial _(+1 example line in PDF)_
    - `16.11.02` large genomic changes _(+1 example line in PDF)_
    - `16.11.03` enzymatic/metabolic _(+1 example line in PDF)_
    - `16.11.04` structural protein disorders _(+1 example line in PDF)_
    - `16.11.05` intracellular/extracellular transport receptors _(+1 example line in PDF)_
    - `16.11.06` triplet repeat/RNA disorders _(+1 example line in PDF)_
  - `16.12` Adverse effects of drugs on multisystem disorders
    - `16.12.01` drug-induced electrolyte abnormalities and acid base-disorders (eg, albuterol; prednisone; diuretics; alcohol; drugs inducing polydipsia, SIADH, or diabetes insipidus)
- `17` **Biostatistics, Epidemiology/Population Health, & Interpretation of the Medical Literature**
  - `17.01` Epidemiology/population health
    - `17.01.01` Measures of disease frequency _(+1 example line in PDF)_
    - `17.01.02` Measures of health status _(+1 example line in PDF)_
    - `17.01.03` Survival analysis interpretation (eg, Kaplan-Meier curve)
    - `17.01.04` Composite health status indicators, measures of population impact _(+1 example line in PDF)_
    - `17.01.05` Population pyramids and impact of demographic changes
    - `17.01.06` Disease surveillance and outbreak investigation _(+1 example line in PDF)_
    - `17.01.07` Communicable disease transmission _(+1 example line in PDF)_
    - `17.01.08` Points of intervention _(+1 example line in PDF)_
  - `17.02` Study design, types and selection of studies (includes dependent/independent variables)
    - `17.02.01` Descriptive studies (case report [one person]/case series [more than one])
    - `17.02.02` Analytical studies: observational _(+2 example lines in PDF)_
    - `17.02.03` Analytical studies: interventional _(+1 example line in PDF)_
    - `17.02.04` Systematic reviews and meta-analysis _(+1 example line in PDF)_
    - `17.02.05` Obtaining and describing samples, matching, inclusion/exclusion criteria, selecting appropriate controls for studies, lack of controls, concealed allocation, randomization, stratification
    - `17.02.06` Methods to handle noncompliance _(+1 example line in PDF)_
    - `17.02.07` Qualitative analysis
  - `17.03` Measures of association
    - `17.03.01` Relative risk
    - `17.03.02` Odds ratio, hazard ratio
    - `17.03.03` Other measures of association _(+1 example line in PDF)_
  - `17.04` Distributions of data
    - `17.04.01` measures of central tendency; measures of variability; regression to mean; normal distribution; nominal measurement
  - `17.05` Correlation and regression, uses and interpretation
    - `17.05.01` correlation coefficients; multiple regression
  - `17.06` Principles of testing and screening
    - `17.06.01` Properties of a screening test _(+1 example line in PDF)_
    - `17.06.02` Sensitivity and specificity; predictive value, positive and negative
    - `17.06.03` ROC curves
    - `17.06.04` Probability _(+1 example line in PDF)_
  - `17.07` Study interpretation, drawing conclusions from data
    - `17.07.01` Causation _(+1 example line in PDF)_
    - `17.07.02` Chance _(+4 example lines in PDF)_
    - `17.07.03` Interpretation of graphs/tables and text
    - `17.07.04` Bias, confounding, and threats to validity (includes methods to address) _(+3 example lines in PDF)_
    - `17.07.05` Internal vs external validity _(+1 example line in PDF)_
    - `17.07.06` Statistical vs clinical significance; clinical and surrogate outcome/end point
  - `17.08` Clinical decision making, interpretation and use of evidence-based data and recommendations
    - `17.08.01` application of study results to patient care and practice, including patient preferences and individualization of risk profiles; risk/benefit analysis; synthesis of concepts with real data
  - `17.09` Research ethics
    - `17.09.01` Informed consent for research
    - `17.09.02` Privacy of patient data (HIPAA)
    - `17.09.03` Roles of institutional review boards (IRBs)
    - `17.09.04` Interim analysis _(+1 example line in PDF)_
    - `17.09.05` Regulatory issues _(+1 example line in PDF)_
    - `17.09.06` Other issues related to research ethics
- `18` **Social Sciences**
  - `18.01` Communication and interpersonal skills, including health literacy and numeracy, cultural competence
    - `18.01.01` Patient interviewing, consultation, and interactions with the family (patient- centered communication skills) _(+6 example lines in PDF)_
    - `18.01.02` Use of an interpreter or surrogate
  - `18.02` Medical ethics and jurisprudence, include issues related to death and dying and palliative care
    - `18.02.01` Consent/informed consent to treatment, permission to treat (full disclosure, risks and benefits, placebos, alternative therapies, conflict of interest, and vulnerable populations)
    - `18.02.02` Determination of medical decision-making capacity/informed refusal
    - `18.02.03` Involuntary admission
    - `18.02.04` Legal issues related to abuse (child, elder, and intimate partner) _(+2 example lines in PDF)_
    - `18.02.05` Birth-related issues
    - `18.02.06` Death and dying and palliative care _(+8 example lines in PDF)_
    - `18.02.07` Physician–patient relationship (boundaries, confidentiality including HIPAA, privacy, truth-telling, other principles of medical ethics [eg, autonomy, justice, beneficence, nonmaleficence])
    - `18.02.08` Impaired physician, including duty to report impaired physician
    - `18.02.09` Negligence/malpractice, including duty to report negligence and malpractice
    - `18.02.10` Physician misconduct, including duty to report physician misconduct
    - `18.02.11` Referrals
    - `18.02.12` Cultural issues not otherwise coded
  - `18.03` Systems-based practice (including health systems, public health, community, schools) and patient safety (including basic concepts and terminology)
    - `18.03.01` Complexity/systems thinking _(+4 example lines in PDF)_
    - `18.03.02` Quality improvement _(+18 example lines in PDF)_
    - `18.03.03` Patient safety _(+52 example lines in PDF)_
  - `18.04` Health care policy and economics
    - `18.04.01` Health care policy _(+5 example lines in PDF)_
    - `18.04.02` Health care economics/Health care financing _(+6 example lines in PDF)_
