# Segment 11 — M2: Onboarding, plan generator, diagnostic, Today dashboard, `coach` graph

**Implementer:** `implementer-sonnet` (behaviour and the reference output are pinned below). **Reviewer:** `reviewer-opus`.
**Wave:** D (parallel with S09, S13). Contracts on the ledger with `seg09`: `naadi_api/analytics/` (accuracy by taxonomy, readiness inputs) and timed-block creation for the diagnostic. Claim the next free Alembic revision on the ledger. Local e2e: `E2E_PORT=7932` (uses 7932–7934), `NAADI_PG_PORT=54432`, `NAADI_STORAGE_PORT=54532`, containers `pg-seg11` / `st-seg11`.
**Depends on:** S02 (`profiles`, `plan_templates`/`plan_template_days`, `plan_items`), S03 (`/api/me`, `TodayPage` countdown), S04 (Aegra crons, `llm.py`), S05 (template `step1-60d` from `data/study_plan_step1_60d.csv`, taxonomy weights), S06 (reading positions → Resume), S08 (blocks), S09 (timed mode, analytics), S10 (`GET /api/cards/stats`).
**HANDOFF refs:** §8 rows Onboarding, Main/PhoneToday, Planner ("generator reproduces the CSV for her inputs"); §6.1 structured role and free-tier rules; §4 colour roles. `docs/AGENTS.md` → `coach`. Boards: `design/Onboarding.dc.html`, `design/Main.dc.html`, `design/PhoneToday.dc.html`.

## Scope
### Data (one Alembic migration with RLS; add new tables to `USER_TABLES` in live_check + the RLS coverage test)
- `study_plans` (id, user_id, template_id, inputs jsonb, generator_version, active, created_at; one active per user) and `study_plan_days` (user_id, plan_id, day_no, date, weekday, week, phase, chapter_ref, chapter_ids, topic, chapter_questions, qbank, self_assessment, admin_task, extra_resource, target_hours, template_day, status default 'Not started', score_notes). `plan_items` gains `plan_id`, `plan_day`.
- `coach_suggestions` (id, user_id, for_date, kind, title, body, facts jsonb, action jsonb, source 'llm'|'rules', status new|accepted|dismissed, created_at). Client may update `status` only.
- `app_config`: `plan` (`reference_hours: 10`, `min_study_days: 21`) and `coach` (`enabled`, `max_suggestions: 3`, `max_users_per_run`) keys with pydantic schemas; flag `coach`.
### Plan generator (`naadi_api/plan/generator.py`, pure function, no I/O)
- Inputs: template days, `start` (onboarding date in the user's timezone), `exam_date` E, `study_days` W, `daily_hours` H. Dates D = start .. E−1; available A = dates whose weekday ∈ W; n = |A|.
- **Reference inputs** (must reproduce `data/study_plan_step1_60d.csv` byte for byte, CRLF, via `csv.writer(lineterminator="\r\n")`, hours printed as `8` not `8.0`): exam `step1`, start **2026-11-16**, exam date **2027-01-15**, W = Mon–Sun (all 7), H = **10** (the template's daily maximum). Then n = 60 and template day k maps to A[k−1].
- n > 60: insert n−60 extra "Review" days (a copy of the template's weekly review row) evenly through both phases. n < 60: drop template "Review" rows, then dedicated "Mixed review" rows, then merge adjacent topic days of the same chapter (topics joined with " + "); never drop or merge day 1 (baseline), assessment rows (`self_assessment` set) or the final rest day, which always maps to E−1. n < `min_study_days` → 422 with a clear message; the profile is still saved.
- Week = ((date − start).days // 7) + 1; weekday from the date; target hours = template × H / `reference_hours`, rounded to 0.5, min 1 (rest day keeps its own value). Non-study weekdays get no items.
- Items per day (`plan_items`, origin 'generator'): read (chapter_ids), questions (target_count parsed from "2 blocks × 40" etc.), assessment, cards, admin, rest. Text stays verbatim from the template; external resources (UWorld, NBME, AnKing) show an "outside Naadi" chip and no link; Naadi equivalents (Qbank builder prefilled, Cards) link in.
### API
- `PUT /api/onboarding` `{exam, exam_date, medical_school?, daily_hours, study_days, materials[]}` → upserts `profiles` (`settings.onboarding` holds school, materials, completed_at), generates and stores the plan, returns a preview ("11 weeks, six days a week"). `GET /api/plan/preview?...` for live preview while editing. `GET /api/plan` / `GET /api/plan/export.csv`.
- `POST /api/diagnostic` → a 40-question (`exam_rules.diagnostic_questions`) **timed** block: stratified by system in proportion to the midpoint of official `weight_min/max`, excluding mock questions, difficulty mix by availability, deterministic per user seed; idempotent (returns the open diagnostic if one exists).
- `GET /api/today` (one round trip, SQL aggregates): countdown, now/next plan items, resume target (last reading position or open block), readiness, by-system % correct (last 14 days), cards due (S10), coach suggestions, pathway strip only when the `pathway` flag is on. Target < 1.5 s warm (TanStack Query cache + `staleTime`).
- Readiness = blueprint-weighted % correct over first attempts in the last 60 days, weighting each system by its official weight midpoint; returned with coverage; `null` until ≥ 40 answered and ≥ 50 % of weighted systems covered.
- `POST /api/coach/suggestions/{id}/accept|dismiss`; accept applies the stored `action` (e.g. add plan items, build a weak-spot block via S08).
### `coach` graph (Aegra cron, nightly 21:07 UTC ≈ 02:37 IST)
- Per user: compute facts in Python (weak taxonomy cells, misses, changed answers, behind/skipped plan days, card retention); one `structured`-role call through `llm.py` (purpose `job`, PII stripped: facts are numbers and taxonomy labels only) → ≤ 3 suggestions of fixed kinds (`catch_up_block`, `review_misses`, `cards_catchup`, `reread_section`) validated by pydantic; every number in the text must appear in `facts` or the suggestion is dropped. Budget exhausted or validation failure → rules-based suggestions from `ui_copy` templates (`source: rules`). Stops when the job reserve is spent; never more than one LLM call per user per night (one retry). Web Push nudges come with S16.
### Web
- **Onboarding** (`/welcome`, shown until `settings.onboarding.completed_at`): five steps per board (Goal, Schedule, Materials, Diagnostic, Plan) on one scrollable page on iPad; exam choices = `exam_rules.exams` that have a plan template (Step 1 today; others hidden, not disabled); date, school, hours slider, study-day chips (selected = filled + check, not colour only), materials chips, live plan preview; lime CTA "Take the diagnostic", secondary "Later". ≤ 3 minutes measured in e2e.
- **Today** (`/`) per Main/PhoneToday: countdown, one lime CTA for the current item (Resume lesson / Start block), today's flow in plan order with durations (no invented clock times), readiness ring with its label and coverage (hidden until non-null), by-system bars (amber + "weak" under 55 %; "not enough data" under 5 attempts), cards due → Review, coach card in violet with "Add to plan" / "Show me why" ("Ask Naadi" only when the assistant flag is on). "Full plan" hidden until S16.
## Tests
- Golden: generator output for the reference inputs == `data/study_plan_step1_60d.csv` bytes; property tests for n ∈ [21, 180] and every W (anchors kept, order kept, no assessment dropped, dates within range).
- Unit: diagnostic stratification and determinism, readiness math, coach fact validator (invented number → dropped), rules fallback, budget exhaustion path (mocked LLM).
- db: RLS on new tables; `/api/today` numbers equal direct SQL on fixtures; coach cron writes ≤ 3 rows within the budget ledger.
- e2e (3 browsers × 6 viewports, frozen-DOM axe): new user → onboarding ≤ 3 min → diagnostic created → Today shows real numbers; Today p95 < 1.5 s warm.
## Acceptance
1. Generator reproduces the CSV for the reference inputs; other inputs yield valid plans.
2. Onboarding ≤ 3 min; 40-question mixed diagnostic; Today one lime CTA, real numbers only, < 1.5 s warm.
3. `coach` cron writes validated suggestions within budget; zero axe violations / console errors.

## Decisions (lead to check)
- Reference inputs inferred from the CSV (dates, 7 study days, 10 h max); the handoff only says "her inputs".
- New `study_plans`/`study_plan_days` tables hold the CSV-shaped day rows (needed for the round trip and S16).
- Materials and the diagnostic do not reorder the plan; they feed links, Today and the coach (S16 rebalances).
- Only exams with a seeded template are offered (Step 3, OET hidden); readiness is weighted accuracy with no pass prediction.
