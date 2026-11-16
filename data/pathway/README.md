# Naadi Pathway module: seed content

Seed data and guides for the **Pathway** module. It covers the route from an Indian MBBS to US residency and beyond: eligibility, exams, OET/Pathway, USCE, ERAS, Match, J-1/H-1B, arrival, residency, licensure, post-residency, and California. All content was built from the verified research of 15 Nov 2026:

- `/home/claude/reports/India MBBS to US residency roadmap.md`
- `/home/claude/research_notes/India MBBS to US residency roadmap/*.md`
- `/home/claude/research_notes/Naadi website build handoff/landing_in_usa.md` (including "Gap-fill (pass 2)")

Generator: `/home/claude/handoff/tools/pathway_build/build.py`. Edit the content modules there (`urls.py`, `stages.py`, `tasks_*.py`, `guides.py`), then rerun `python3 build.py`. The script regenerates every file below except this README, and it fails or prints problems if validation breaks.

## Files

| File | Loads into | Contents |
|---|---|---|
| `pathway_stages.json` | `pathway_stages` | 16 stages: `{key, sort, title, summary_md, official_links:[{label,url}], last_verified}` |
| `pathway_tasks.json` | `pathway_tasks` | 186 tasks: `{id, stage_key, sort, title, detail_md, due_rule, cost_usd, official_url, last_verified}` |
| `00-overview.md` | guide (cross-cutting) | Master timeline, budget to Match Day, 2026 policy watch, best/typical/worst scenarios |
| `01-eligibility.md` … `16-california-bay-area.md` | one guide per stage | Summary, checklist table (task · when · cost · link), task details, stage-specific tables, common mistakes, official links, sources |

Stage order (`sort` 10–160): `eligibility`, `step1`, `oet_pathway`, `step2ck`, `usce`, `specialty_choice`, `eras_application`, `interviews_ranking`, `match_day`, `visa_j1`, `pre_departure`, `arrival_first_30_days`, `residency_life`, `step3_and_licensure`, `post_residency`, `california_bay_area`.

Note: `schema.sql` comments list older example keys (`eras`, `match`, `visa`, `arrival`, `residency`). The seed uses the keys above. Update the comment, or map the keys, before loading.

The key tables live in these guides:

| Table | Guide |
|---|---|
| First-year budget (Bay Area vs mid-cost city) | `16-california-bay-area.md` |
| Bay Area PGY salaries | `13-residency-life.md`, `16-california-bay-area.md` |
| HUD FY2027 rents, with the verify note | `16-california-bay-area.md` |
| Visa best / typical / worst scenarios | `10-visa-j1.md`, `15-post-residency.md`, `00-overview.md` |
| 2026 policy watch: H-1B $100k fee litigation, J-1 fixed admission-period rule postponed, MoHFW Statement of Need ₹10 lakh bond, visa appointment issues, and more | `10-visa-j1.md`, `15-post-residency.md`, `00-overview.md` |

## `due_rule` grammar

`<anchor>[+|-<N>d]`, `date:YYYY-MM-DD` (hard external deadline), or `annual:MM-DD` (repeats every year).

| Anchor | Meaning (the user sets it, or the app infers it) |
|---|---|
| `start` | the day the user starts the Pathway in Naadi |
| `oet_date` | OET test date |
| `step1_date` / `step2ck_date` | booked exam dates |
| `observership_start` | first observership start |
| `eras_open` | MyERAS opens (early June; 2027 cycle: 4 Jun 2026) |
| `eras_submit` | applicants may submit (2027 cycle: 2 Sep 2026) |
| `rol_deadline` | NRMP Rank Order List deadline (2027 Match: 3 Mar 2027) |
| `match_day` | NRMP Match Day (2027: 19 Mar 2027) |
| `arrival` | US arrival date |
| `residency_start` / `residency_end` | DS-2019 program start and end |

`date:` rules pin the 2027-Pathway / 2028-Match cycle, for example the OET booking close on 2026-11-25 and the Pathway deadline on 2027-01-31. They must be rolled forward each cycle.

## Content rules

- Every fact in `detail_md` and `summary_md` has an inline `[label](url)` link to the research source. Facts the research could not confirm from a primary source say **(verify)**. Naadi judgments and inferences are labelled as such ("inference", "Naadi estimate", "unsourced planning estimate").
- `cost_usd`: a number for a sourced fee. `0` means no fee, or an information-only task. `null` means the cost varies or was not sourced. INR figures use about ₹89/USD.
- Secondary sources (law-firm blogs, aggregators, news) are named as such in the text.

## Re-verification

Every stage and task carries `last_verified` (currently `2026-11-15`). This content goes stale quickly: fees, ECFMG Pathway seasons, visa rules and litigation all change.

- **Quarterly:** re-verify every task and stage against its `official_url` and inline sources. Update `last_verified` only on rows you actually re-checked. Suggested cadence: Feb, May, Aug and Nov, aligned with the ECFMG Pathway season, MyERAS opening, ERAS submission and the Match calendar.
- **Immediately on policy news:** re-verify the affected stages the same day for any of these events:
  - an ECFMG Pathways or 2028-season announcement
  - FSMB/USMLE fee or format changes
  - NRMP or AAMC calendar releases
  - State Department visa-appointment or fee changes, including the $250 Visa Integrity Fee
  - H-1B $100k fee court rulings or rules
  - the DHS duration-of-status rule taking effect
  - MoHFW Statement of Need guideline changes
  - HHS or Conrad 30 waiver changes
  - a new Visa Bulletin
  - California licensure bills
- Suggested app behaviour: show a "verified on" date on every task, and flag any row whose `last_verified` is more than 100 days old. Consider a `pathway_watch` list keyed to the policy items in `00-overview.md`.

## Known gaps (not published as fact)

The research did not source these, so the seed does not include them:

- NRMP registration fee
- MoHFW Statement of Need fee and processing time
- Intealth J-1 sponsorship fee
- California PTL fee
- Step 3 fee (~$955, shown with "unverified")
- India FEMA cash-export limit
- whether an Indian license or IDP can be used to drive in California
- W-4 nonresident rules
- California state tax treatment of treaty benefits
- car insurance, phone plans and renter's insurance
- Central Valley program salaries
- the 457(b) limit
- BLS/ACLS, drug-screen and background-check rules
- interview format (virtual vs in person)
- ERAS 2028 and Match 2028 dates (shown as inferred)
