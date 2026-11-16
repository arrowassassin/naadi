# Segment 16 — M5: Planner screen, `planner` rebalance graph, ICS feed, Web Push

**Implementer:** `implementer-sonnet` (the generator exists from S11; rebalance rules are pinned below). **Reviewer:** `reviewer-opus`. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** F (with S17, S18). Contracts on the ledger: with `seg18` the service worker file (`apps/web/src/sw.ts`: this segment creates it with push handlers only; S18 adds caching) and the outbox entity `plan_item` (status changes are LWW per row with `client_updated_at_ms`); with `seg15` placement `planner_items` (timetables → plan items proposal). Claim the next free Alembic revision. Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg16`).
**Depends on:** S04 (Aegra interrupts + crons), S08 (Qbank builder prefill), S09 (assessment blocks, `naadi_api/analytics/`), S10 (cards stats), S11 (`naadi_api/plan/generator.py`, `study_plans`, `study_plan_days`, `plan_items` with `plan_id`/`plan_day`, `coach` graph, Today "Full plan" link hidden until now), S14 (`add_to_plan` tool), S15 (placement registry).
**HANDOFF refs:** §8 Planner/PhonePlan row (rebalance without breaking assessment days, ICS feed URL, Web Push for home-screen PWA iOS 16.4+), §5.2 (offline writes go through the outbox), §4 colour roles. `docs/AGENTS.md` → `planner`, `coach` (Web Push nudges). Boards: `design/Planner.dc.html`, `design/PhonePlan.dc.html`, `design/Main.dc.html` (Today "Full plan"), `design/Settings.dc.html` (study plan + reminders group).

## Scope
### Data (one migration with RLS; update `test_rls_coverage.py` + `live_check.py`)
- `plan_items` gains `client_updated_at_ms bigint`, `moved_from date`, `rebalance_id uuid`. Client may update `status` only, through the API.
- `plan_rebalances` (user table): `id`, `user_id`, `created_at`, `trigger ('missed'|'student'|'coach')`, `proposal jsonb` (moves, drops, warnings), `status ('proposed'|'accepted'|'declined'|'expired')`, `applied_at`. Client select only.
- `calendar_feeds` (user table): `user_id`, `token_hash` (SHA-256 of a 32-byte random token), `created_at`, `revoked_at`. Client select only; the token is shown once.
- `push_subscriptions` (user table): `id`, `user_id`, `endpoint` (https only), `p256dh`, `auth`, `ua_label`, `created_at`, `last_ok_at`, `failures`. Client select + delete own; insert via the API.
- `app_config.planner`: `missed_after_hours: 6`, `max_items_per_day_factor: 1.25`, `reminder_local_time: "07:30"`, `push_max_per_day: 2`; flag `planner` on at the end; `ui.nav` gains Plan; `ui_copy` `planner.*`, `push.*`.
### Rebalance (`naadi_api/plan/rebalance.py`, pure function, no I/O, no LLM)
- Input: active plan days, items with status, `today` in the user's timezone, study days, exam date E. Missed = `planned`/`now` items on days before today (after `missed_after_hours`).
- Rules: assessment items and assessment days never move and nothing new is placed on them; the final rest day stays on E−1; moved items keep their order and go to the earliest study days from today up to the next assessment day; a day takes at most `target_hours × max_items_per_day_factor`; if they don't fit, drop planned "Review"/"Mixed review" items in that window first (listed as drops), then spill past the assessment day only for read/cards items, never questions that the assessment depends on; anything still unplaced is a warning ("3 items don't fit before your exam"), never silently dropped. Output is deterministic.
- **`planner` graph** (Aegra, `aegra.json`): computes the proposal, writes `plan_rebalances`, then `interrupt()` with the proposal; on accept applies it in one transaction (`moved_from`, `rebalance_id`, `origin` unchanged); decline marks it declined. Triggers: student "Rebalance", the nightly `coach` run when missed items exist (coach suggestion links to it), and the tutor. Zero LLM calls; summary text from `ui_copy` templates.
### API
- `GET /api/plan/week?start=` (7 days, items, hours, assessment flags, milestones), `PATCH /api/plan/items/{id}` `{status, client_updated_at_ms}` (LWW: newer wins, tie → server), `POST /api/plan/items` (student items), `POST /api/plan/rebalance` (starts the run, returns the thread for the confirm), `GET /api/plan/rebalances?status=proposed`.
- ICS: `POST /api/plan/calendar-feed` (rotate: revokes the old token, returns the URL once), `DELETE` (revoke), `GET /api/plan/ics/{token}` (no auth header; token checked by hash; `icalendar` pinned; all-day `VEVENT` per day with item titles and durations, stable `UID`s, `X-WR-CALNAME`; no names, emails or notes; rate limited; `Cache-Control: private, max-age=900`).
- Web Push (`pywebpush`, pin; VAPID keys `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` via `settings.py`, in `docs/SECRETS.md` + `check_env.py`): `GET /api/push/key`, `POST /api/push/subscriptions`, `DELETE /api/push/subscriptions/{id}`, `POST /api/push/test`. A daily cron at `reminder_local_time` per user sends one reminder (today's first item) and the coach nudge, ≤ `push_max_per_day`; 404/410 from the push service deletes the subscription. Payloads hold a `ui_copy` key + numbers + a deep link, never note text.
### Web
- **Planner** (`/plan`) per board: "Week n of m", 7-day calendar of study blocks with hours, assessment days marked with a word and icon, road-to-exam milestones, item sheet (start → deep link into Reader / Builder prefilled / Cards; mark done / skip), "Behind" items in amber with the word, rebalance suggestion card in violet with Accept / Show changes / Decline (diff list: moved from → to, drops, warnings). One lime CTA: today's next item. Phone per PhonePlan: day header, week strip, day list, rebalance prompt.
- Settings → Study plan: calendar feed (copy URL, rotate, revoke; webcal link), reminders (enable push → permission → subscribe; test notification), shown only when `PushManager` exists and the app runs standalone; otherwise the reason ("Add Naadi to your Home Screen to get reminders") with the steps.
- `apps/web/src/sw.ts` via `vite-plugin-pwa` 2.0.0 `injectManifest`: `push` and `notificationclick` (focus or open the deep link) only. Web app manifest (name, icons from tokens, `display: standalone`, theme/background from tokens).
- Today's "Full plan" link and the coach "Add to plan" route turn on.
- States: loading, empty (no plan → onboarding link), error, offline (status changes queue with "will sync"; rebalance needs a connection: disabled with reason).
### Guardrails (review blockers)
- No placeholders or fake events; only real plan rows render. Every UI string a `ui_copy` key; pydantic → OpenAPI → zod.
- Agent writes (rebalance, `add_to_plan`) wait for an interrupt confirm. No LLM call is added. The ICS feed and push payloads carry no PII and no question content.
- Lime = one next action per view, violet = rebalance proposals, amber + word for behind; status never colour-only.

## Tests
- Unit (pytest): rebalance property tests (assessment days never touched, E−1 rest kept, order kept, capacity respected, nothing silently dropped, deterministic) over generated plans for n ∈ [21, 180]; golden case: 3 missed days in week 4 of the reference plan; ICS output validated by `icalendar` parse + stable UIDs; push 410 cleanup (mocked endpoint); LWW on item status.
- db: RLS on the four tables; ICS token by hash only, revoked → 404; client cannot insert subscriptions or rebalances directly.
- e2e (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`): miss two days (frozen clock) → rebalance proposal → Show changes → Accept → calendar shows moved items, assessment day unchanged; Decline leaves the plan as it was; ICS URL fetch parses; Chromium: SW receives a synthetic `push` and shows a notification (device checklist covers the installed iPad PWA).
## Acceptance
1. Rebalance moves missed items forward without breaking assessment days, only after confirmation.
2. The ICS URL subscribes in Calendar; Web Push is delivered on the installed PWA (device checklist).
3. Matches Planner/PhonePlan boards; one lime CTA per view; zero axe violations / console errors.

## Decisions (lead to check)
- Rebalance is a deterministic Python function; the `planner` graph only proposes, interrupts and applies (zero LLM calls).
- New tables `plan_rebalances`, `calendar_feeds`, `push_subscriptions`; new secrets `VAPID_*`. S16 creates `sw.ts` (push only); S18 extends it with caching.
- PWA install, and so Web Push on iPad, needs a public Space or custom domain (PLAN risk 3): the owner decides before this lands.
