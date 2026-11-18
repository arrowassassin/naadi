# Naadi: handover to the next coding agent (written 2026-11-18)

You are taking over a nearly finished build. The previous agent (a lead plus about 30 parallel Claude cloud
sessions) stopped because its weekly usage limit ran out (it resets **2026-11-23 09:00 UTC**). This file is the
complete context. Read it top to bottom once, then follow section 9.

Naadi is an iPad-first USMLE Step 1 + Step 2 CK study and career platform. Ground truth for product and
engineering is `handoff/HANDOFF.md`, `CLAUDE.md` (working agreement), `docs/PLAN.md` and `docs/segments/*.md`.
This file only adds what happened, what is live, and what is left.

## 0. Rules that never change

- Free tier only: `:free` OpenRouter models, local fastembed embeddings, Tavily via `search_cache`. All LLM calls
  go through `apps/api/naadi_api/llm.py` and the budget guards in `config/models.yaml`. Strip PII before any LLM call.
- Never reveal a question's answer or explanation before the student answers. The assistant is disabled in exam mode.
- Content legality: every media or text asset references a `sources` row. Never add copyrighted text (First Aid,
  UWorld, AMBOSS, NBME, Boards and Beyond, Pathoma, Sketchy, StatPearls text). Only storable licences are
  published (public domain, CC0, CC BY, CC BY-SA in its own partition, Naadi original). NC, ND and link-only
  sources are links only. YouTube only through the official player (see S20).
- DB changes only through Alembic migrations, with RLS and grants in the same migration. Single head (now `0039`).
  Claim the next number before you write it.
- Colour roles: lime = the one next action per view, violet = assistant, paper = student. Status never by colour alone.
- Every user-facing string is a `ui_copy` key (config + `REQUIRED_UI_COPY_KEYS`); server responses carry structured
  fields, not English sentences. Generated files (`ui-copy.defaults.json`, `openapi.json`, `gen/**`,
  `requirements.txt`, `section_bodies.json`, `cards.generated.json`) are never hand-merged or hand-edited: regenerate.
- Never commit or print secrets (names only in section 7). Never log request bodies with uploads or notes.
- iPad Safari is the primary target. Test every UI change at iPad portrait and landscape, phone 390/412 and laptop, in
  Playwright WebKit and Chromium.
- Git: work on `claude/blissful-knuth-1gh22d`. Never force-push. No PRs and no merge to `main` unless the owner asks.
  The ambient `DATABASE_URL`, `SUPABASE_URL` and `APP_BASE_URL` in this environment point at the **hosted** project:
  for tests export loopback values and never run tests against the hosted database or Space.

## 1. Repos, branches, live system

| Thing | Where |
|---|---|
| Code | https://github.com/arrowassassin/naadi (public) |
| Working branch (deployed) | `claude/blissful-knuth-1gh22d` |
| Mirror used by the parallel agents | `claude/naadi-shared` (same content plus a few later commits) |
| Task board and messages | branch `claude/naadi-ledger`, read with `python3 .claude/skills/multiplayer/scripts/multiplayer.py tasks` / `inbox --as lead --all` / `show <message-id>` (plain Python 3) |
| Unintegrated content drafts | branches `work/seg23-a`, `-b`, `-c`, `-e`, `-f`, `-g` (see section 4) |
| Live app | https://arrowassassin-naadi.hf.space, public HF Docker Space `arrowassassin/naadi`, version `afab687` |
| YouTube player (separate origin) | static Space `arrowassassin/naadi-player`, source `deploy/player-space/`, deployed by its own script |
| Database | Supabase Postgres (hosted), Alembic head `0039`, 80 tables, RLS on all |

Live state on 2026-11-18 (verified): health `ok` except `backup` and `searxng` (degraded, expected: no backup
token, no SearXNG sidecar on the free CPU Space). Content in the DB: 118 chapters, 573 sections, **2,704 questions
(2,652 published; 52 held `in_review`)**, 3,171 flashcards, 7 content packs, 688 taxonomy nodes. All feature flags
are ON for everyone except `ink_lab` and `authoring` (admins). `generation` is ON but shows "not configured".
Supabase Auth Site URL and redirect URLs are set to the live site. `live_check --seed` passes 37/38: the one
failure ("questions published >= 2704, got 2652") is the 52 intentionally held figure questions (section 4).

**Still running when this was written (2026-11-18 01:20 UTC):** one Claude cloud session on Sonnet, `integ-green-3`
(session `session_015qGSermQ3QbwBNX57Uzxfe`, branch `work/integ-green-3`), re-running CI's database and e2e suites
locally to make `claude/naadi-shared` green. It posts findings and fixes on the ledger and pushes to
`claude/naadi-shared` only. Check `multiplayer.py inbox --as lead --all | tail` before duplicating its work, and
merge its fixes into the working branch (`git cherry-pick` the commits, never force-push). The lead's scheduled
check-ins are stopped, so nothing else will wake the previous agent. The weekly limit that stopped the Opus
sessions did not stop Sonnet sessions, so unfinished cloud sessions can also be continued on Sonnet.

## 2. Segment status

Verified and deployed earlier (nothing open): S02 db, S03 auth + shell, S04 agent platform, S05 seed, S06 reader,
S07 search, S08 qbank, S09 exam, S10 cards/FSRS, S11 onboarding/today/coach, S12a ink engine.

Everything below is **in the deployed build** but its CI was never green (section 5) and review state differs:

| Seg | What | Review state at hand-off |
|---|---|---|
| S12b | Ink persistence + sync | APPROVED (`review12b`, 052e282). Two LOW silent-data-loss items were requested, unverified: (A) a stroke drawn offline >90 days ago is dropped silently; (B) move/recolour/text edits must be stamped `max(now, prev+1)` like new strokes |
| S13 | Notes, notebooks, highlights | CHANGES REQUESTED (`review13`), fixes pushed (b969b21, 6d1302d, 5c01baf), **not re-reviewed**. Items: offline lazy-chunk crash (use `lazyWithFallback`), handwriting-OCR opt-in (owner decision: OFF by default, Settings toggle), bounded LWW for notes, pasted-HTML CSP errors, `/api/notebooks` offline rejection, notebooks.spec:462 |
| S14 | Tutor assistant | APPROVED at d92e265 after 3 rounds (Aegra route allowlist, signed summaries). Later: f2af55c (HMAC binds thread+user), 70f999b, e6aba4b (lazy fallback, a11y). Not re-reviewed after those |
| S15 | Ingest + knowledge base | CHANGES REQUESTED, 6 of 7 fixed (d4d7765). **Open: the 30-prompt model_eval rubric** is only partly run (100/100 MCQ done; rubric limited by the free daily cap of 15 calls/UTC day; resumable checkpoint exists). Not re-reviewed |
| S15b | Image/video generation | Reopened with 5 items, fixed (0f22e497, be208fe: its approve route removed, S19's queue is the single approval path). Not re-reviewed. Needs the owner (section 8) to show anything |
| S16 | Planner | APPROVED (c1d1fbe) conditional on CI |
| S17 | Pathway + vault + cost tracker | APPROVED (fc646d7) conditional on CI. Lows requested (config.spec isolation, sources); unverified |
| S18 | Offline-first PWA | Done (f4503936, c988af2f), precache 4.35 MB. **Never reviewed** (the reviewer hit the limit). High risk: sync correctness, no `/api` caching, per-user data cleared on sign-out |
| S19 | Admin console | APPROVED (777cd9c); follow-ups R1-R4 integrated (c6aaac82), not re-reviewed. Open: `/admin/questions/<id>` fails for every item on a minimal local auth stack (reported by review23-d) |
| S20 | YouTube mapper + Anki | APPROVED conditional on CI (final: 80ec23c) |
| S21 | Authoring pipeline, Settings, export | Built in part (backend landed fc4e65b, web in progress). **Not marked done. Check the ledger and the code** |
| S22 | Hardening + final audit prep | In progress. Done: perf budgets (initial JS 222 KB gz), `lazyWithFallback`, route lazy-loading, supabase-js deferred. Open: size/Lighthouse gate is report-only (`PERF_GATE`), chapter LCP target <2.5 s not met, remaining raw `lazy()` sites, per-route offline e2e, CSP/Trusted Types sweep, backups, recovery, the live-auth e2e token leak (below) |
| UXF1 | Owner-reported iPad fixes (Contents sheet double scrollbar, Welcome date/college overlap) | On the branch (d37fbcb). Not verified on a real iPad |
| S23 | Content library (section 4) | Foundation partly landed, 4 of 7 packs in the build, defects open |

## 3. Known defects and to-dos (highest first)

1. **Make CI green** (section 5). Every finished GitHub run on the shared tip so far was red.
2. **Security: the live-auth e2e leaks `HF_TOKEN`.** `apps/web/e2e-live/live-auth.spec.ts:26` exchanges the token
   through Playwright's request context; on failure Playwright writes the `Authorization` header into
   `error-context.md`. Do the exchange in Node `fetch` outside Playwright, pass only the short-lived `__sign` value,
   scan `test-results` for secret env values after the run, and drop `e2e-live` error-context files from CI artifacts.
3. **Rotate secrets** (owner, section 8): the HF token was printed once in a local test log and in tool output.
4. **Medical review of the new content** (section 4): zero independent approvals so far, four zero-tolerance hits seen.
5. Review S13, S15, S15b, S18, S19 follow-ups, S21, S22 with the same rigour as the approved ones (use
   `docs/REVIEW_CHECKLIST.md`).
6. S22 gate: flip `PERF_GATE=enforce` last, after chapter LCP < 2.5 s (notes/ink engines load after first paint) and
   the offline-reload installability step passes.
7. Optional hardening: bind seeds and migrations to a pre-deploy backup; the hosted project has no backup job (S22).

## 4. Content library (S23): what exists and what is wrong

Plan and rules: `docs/segments/23-content-library.md`. Author guide: `docs/CONTENT_AUTHORING.md`. Research and
licences: `docs/research/*.md`. Target (owner default): about 4,000 original questions (about 2,000 per Step, at least
6 per level-3 outline area per Step, weighted to exam percentages), full study text and cards for every area of the
USMLE 2026 outline (18 systems, 147 categories, 276 level-3 areas), 4 full-length practice exams per Step in the 2026
format. Commercial-safe sources only (owner has not confirmed Naadi stays non-commercial). No imported question
sets or Anki decks (MedQA/MedMCQA/AnKing are not usable, see the research files).

In the deployed build (`data/content/<pack>/`, ids prefixed `s23x-`, all items `self_reviewed` only):

| Pack | Items | State |
|---|---|---|
| `cardio-resp` (S23-B, systems 08, 09) | 625 | live. No independent review yet |
| `gi-multisystem` (S23-D, system 10 only) | 280 | live. `review23-d`: minor defects (2 near-duplicate pairs, HCC figure should say ultrasound **plus** AFP per AASLD 2023, 11 length-cued items, UK spellings in 73 items). System 16 (multisystem) is mostly unwritten |
| `neuro-behavioral` (S23-E, system 05 only) | 790 | live. 35 figure items held `in_review`. Not reviewed. Systems 04 (psychiatry) and 05 are both complete |
| `renal-endo-nutrition` (S23-C) | 324 | live. `review23-c` CHANGES REQUESTED on both batches. **Fixed by the lead on 2026-11-18:** DKA/HHS potassium threshold 3.3 to 3.5 mEq/L (commit afab687). **Still open** (owner said ship anyway): two copied commercial mnemonics ("Salt, Sugar, Sex" in `src/15.04.01.json`, "Anterior cools" in figure `s23c-fig1506-1`), GLP-1 RA in T2D+CKD framed as glucose-only (section `s150101-2-1`), the 2024 dextrose and resolution criteria, 4 major + 15 minor defects (ledger `20261117T142213-review23-c-5e524b`, `...142216-review23-c-07c7ca`), key is the longest option in 37% (renal) and 51% (endocrine) of items (target <=25%), 0 key-fact spans, 3 figures with overlaps, `cards.generated.json` stale. Batch 3 (nutrition 16.04, fluids/acid-base 16.07) unwritten |

**Coverage today** (`cd apps/api && uv run python ../../tools/coverage.py`, measured 2026-11-18; "at min" = areas with at
least the minimum 6 items per Step, of all level-3 areas in that system):

- Complete on both Steps: 04 Behavioral Health, 05 Nervous System, 08 Cardiovascular, 09 Respiratory, 10 GI,
  11 Renal, 15 Endocrine (7 of 18 systems).
- Missing or nearly empty (0-3 areas at minimum): 01 Human Development, 02 Immune, 03 Blood, 06 Skin,
  07 Musculoskeletal, 12 Pregnancy, 13 Female reproductive, 14 Male reproductive, 17 Biostatistics/Epi,
  18 Social Sciences. 16 Multisystem has 5 of 28 areas at minimum (Step 1) and 3 of 28 (Step 2 CK).
- Overall: 311 of 556 area-and-Step combinations are below the minimum; item volume is about two thirds of the
  blueprint target (Step 1: 1,420 of 2,151; Step 2 CK: 1,485 of 2,242 item-tags). The coverage gate is report-only
  until `config/content_coverage.yaml` has `s23_complete: true`; set it only when the gaps are closed.
- Not built at all: the 4 full-length practice exams per Step, the official-practice links page, and stem-figure
  rendering (which keeps the 52 held questions offline).

**What you do on your own for content (in this order):** (1) rebase `work/seg23-a`, `-g`, `-f`, `-e` onto the working
branch one at a time, run the validators and `generate_cards.py`, fix problems, load locally; (2) fix the known
review defects above; (3) build stem-figure rendering and publish the 52 held questions; (4) write the missing areas
(systems 16, 01-03, 06-07, 12-14, 17-18) until `coverage.py --enforce` passes; (5) build the practice-form assembler
and the links page; (6) get an independent review of a stratified 15% sample of each batch; (7) deploy and seed
(section 6) after each reviewed batch. Everything except the owner steps in section 8 is yours to do without asking.

Not in the build, **drafts only on branches** (fetch them: `git fetch origin 'refs/heads/work/*:refs/remotes/origin/work/*'`):

- `work/seg23-a`: immune, blood, human development, general principles, about 455 items drafted (111 immune items validated).
- `work/seg23-f`: skin, MSK, pregnancy, reproductive; partial (06.01, 06.02, 06.04, 06.06 complete).
- `work/seg23-g`: biostatistics/epi and social sciences, 772 items planned; systems 17 and 18 merged on the branch.
- `work/seg23-e`, `-c`, `-b`: later checkpoints of the packs above (e: later fixes; c: batch-3 review sweep; b: key-fact spans).
These need rebasing onto the current branch, the validators run, and independent medical review before they go live.

Why 52 questions are held: qbank/tutor/exam never rendered `questions.figure_ids`, so image questions cannot be
shown. The author sessions held them `in_review`. Build the figure rendering (stem figures visible in practice,
tutor and exam and offline device copies, with alt text and attribution; explanation-only figures stay hidden until
after the attempt, using the existing leak guards, plus an e2e per mode), then publish them.

Foundation (S23-0, `seg23-core`): taxonomy `USMLE-2026` (688 nodes), blueprint targets (587), pack loader/importer
(idempotent), validators in `tools/` and `apps/api/naadi_api/content`, `tools/generate_cards.py`. Still to build:
practice-form assembler (4 forms per Step), coverage admin page and CI gate, official-practice links page, and these
validator additions: test-wiseness (key is longest option > 30%), commercial-mnemonic denylist, US-spelling check,
near-duplicate detector, drift check for generated files. `generate_cards.py --check` currently reports stale
`cards.generated.json` for gi, neuro and renal packs.

## 5. CI

- Workflow `.github/workflows/ci.yml`: checks job, integration (db) job, SearXNG stage, and e2e shards
  (chromium, webkit, firefox, each 1/3 to 3/3). GitHub is public so minutes are free, but there are only a few
  runners, so with many pushes the queue ran about 3 hours behind (31 runs queued at hand-off).
- No fully green run on the shared tip was ever observed. The last finished runs (218-225) were all red; they
  predate the test-isolation fixes `0249297` and `4cec084` (Storage leftovers breaking `test_migrations`, service
  worker bypassing `page.route`). Re-run before concluding anything.
- Failures seen on run 225 (check each is still real): `tests/db/test_migrations` round trip,
  `tests/db/test_seed` live_check, `tests/authoring/test_store_db`, and e2e anki, console-guard, cards:322,
  config.spec, review.spec, pathway.spec:249, plus dependency audit.
- e2e now defaults to `serviceWorkers: "block"`; specs that test the SW opt in with `serviceWorkers: "allow"`.
- **Pending owner approval, not applied** (the tool permission layer blocked it): cancel old queued runs, and make CI
  keep only the newest push waiting per branch. The change:

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}   # was ... || github.sha
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```
  and update `apps/api/tests/test_workflows.py::test_ci_cancels_only_superseded_pull_request_runs` to the same group.

## 6. Runbook

Setup (one time): Node 22 + pnpm, `uv`, Docker, Playwright browsers (`pnpm exec playwright install` if needed).

```bash
pnpm install --frozen-lockfile
cd apps/api && uv sync --frozen && cd ..
# fast gate (what each agent ran before every push)
pnpm -s lint && pnpm -s typecheck && pnpm -s test && uv --directory apps/api run pytest -q -m "not live and not db"
# database tests need a local stack, never the hosted DB
rm -f /var/run/docker.pid; (nohup dockerd >/tmp/dockerd.log 2>&1 &)      # if Docker is not running
NAADI_PG_CONTAINER=pg-naadi NAADI_STORAGE_PORT=54335 apps/api/tests/db/stack.sh up
export DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres NAADI_STORAGE_URL=http://localhost:54335
uv --directory apps/api run pytest -q -m db
# e2e smoke set (docs/REVIEW_CHECKLIST.md), twice, no retries; the full 3x6 matrix is CI's job
pnpm --filter @naadi/web exec playwright test <specs> --retries=0 --workers=2 \
  --project webkit-ipad-portrait --project webkit-ipad-landscape --project webkit-phone-390 --project chromium-laptop-1440
```
Use ports below 32768 and run one e2e shard at a time per box (parallel shards starve the CPU and fake timing failures).
Never run ad-hoc tests on a live shard's `E2E_PORT` (global teardown deletes its auth state).

Deploy to the Space (needs `HF_TOKEN`; run from a clean checkout of the exact commit):

```bash
git worktree add .claude/worktrees/deploy <sha> && cd .claude/worktrees/deploy
scripts/deploy_space.sh --dry-run     # shows the file list
scripts/deploy_space.sh               # uploads, waits for RUNNING, smoke-tests /api/health, /, /api/ready
```
The container runs `python -m naadi_api.agent.migrate_all` at start, then uvicorn. Migrations run on the hosted DB at
that moment, so merge only migrations you have run on a fresh local stack (up/down/up). To roll back code,
redeploy the previous commit (earlier good deploys: `46c0394`, `c7e35c0`, `afab687`); schema is not auto-downgraded.

Seed or re-seed content on the live project (the sandbox cannot reach Postgres directly; the importer runs inside
the Space): `handoff/scripts/seed_hosted.py` (header explains the env vars), then
`uv run python -m tests.db.live_check --seed` (read-only, uses the Supabase Management API).
Without Postgres access, read-only SQL goes through `POST https://api.supabase.com/v1/projects/<ref>/database/query`
with `SUPABASE_ACCESS_TOKEN` (that token is read-only for auth config: it cannot change Auth settings).

Ledger (optional, if you keep working with parallel agents): `.claude/skills/multiplayer/SKILL.md` explains the
commands (`tasks`, `inbox`, `show`, `post`, `assign`, `verify --reopen`, `integrate`, `claims`).

## 7. Secrets (names only; values are in the environment or the Space settings, see `docs/SECRETS.md`)

`HF_TOKEN`, `HF_SPACE_ID`, `HF_BACKUP_DATASET`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_JWT_SECRET`, `SUPABASE_ACCESS_TOKEN`, `DATABASE_URL`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`,
`TAVILY_API_KEY`, `YOUTUBE_API_KEY`, `ADMIN_EMAILS`. Not set yet: `HF_GEN_TOKEN`, `HF_BACKUP_TOKEN`,
`BACKUP_AGE_RECIPIENT`, web-push VAPID keys. `uv run scripts/check_env.py` verifies them without printing values.

## 8. Only the owner can do these

1. Rotate `HF_TOKEN` (and `SUPABASE_SERVICE_ROLE_KEY`); update the Space secrets and GitHub Actions secrets.
2. Approve the CI change in section 5 (or edit the workflow themselves).
3. Decide commercial vs non-commercial (default assumed: commercial-safe) and the question-bank size (default 4,000).
4. Image/video generation: create the Space (`hf repos create arrowassassin/naadi-gen --type space --space-sdk gradio
   --flavor zero-a10g --private`, variable `GRADIO_SSR_MODE=false`), run workflow `deploy-gen`, create a
   fine-grained token scoped to that Space as `HF_GEN_TOKEN`, confirm the LTX-Video licence before enabling video.
5. Backups: `HF_BACKUP_TOKEN` and an `age` recipient `BACKUP_AGE_RECIPIENT`. Push: real VAPID keys.
6. Supabase magic-link email template (optional, helps links opened in another browser): Authentication, Emails,
   Magic Link body link `{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=magiclink`.
7. Real-iPad checks: `docs/DEVICE_CHECKLIST.md` (including the 240 fps Pencil latency run).

## 9. Suggested order for you (one agent, no parallel sessions)

1. Orient: read this file, `CLAUDE.md`, `handoff/HANDOFF.md`, `docs/REVIEW_CHECKLIST.md`. `git log --oneline -20`. Open the live site and click through Today, Learn, Practice, Cards, Notes, Planner, Pathway, Assistant.
2. Get a real baseline: run the fast gate, the db suite on a fresh stack, and the e2e smoke set on the tip. Fix test-infrastructure problems first (isolation, ordering); send product bugs to the right segment's checklist. Never skip or weaken a test.
3. Do the security item (3.2) now, and the S12b lows.
4. Close review items for S13, S15 (finish the eval across days), S19 `/admin/questions/<id>`, then review S18 and S21 properly.
5. Content: rebase the `work/seg23-*` branches one at a time, run validators, fix the known defects (section 4), build the stem-figure rendering, publish the 52 held items, then write the missing areas. Get independent review (a different model or a human clinician) on a stratified 15% sample of every batch before it ships. One zero-tolerance hit (wrong key, unsafe management, outdated guideline, copied expression, unlicensed image) means re-review the whole batch.
6. S22 finish: Lighthouse and size gate to enforce, CSP and Trusted Types sweep, backups, final audit across iPad, phone and laptop.
7. Redeploy after each approved batch, re-run `seed_hosted.py` if content changed, then `live_check --seed`.

## 10. Lessons from the build (save yourself the time)

- Aegra is deny-by-default behind a host middleware (exact allowlist of routes; a test enumerates every mounted
  route). Never let a client send `command.goto/update`, state writes, forks or v2 `/commands`.
- Every lazy chunk goes through `lazyWithFallback` (`apps/web/src/shell/lazyPage.ts`): offline before a chunk loads must
  never crash the page. All script URL sinks go through one Trusted Types policy.
- The YouTube iframe API cannot run in an opaque-origin sandbox; the player runs on its own static Space with exact
  origin checks both ways (`deploy/player-space/`, the deploy script refuses a file with `ALLOW_LOOPBACK=true`).
- A parent CSP of `script-src 'self'` is a hard rule.
- Test isolation: admin specs edit global config (`feature_flags`, nav); restore it, or use per-test students. The
  seeder keeps admin-edited keys, so a polluted local DB makes other specs fail in confusing ways.
- OpenStax moved to CC BY-NC-SA with an AI-use clause in March 2026: link only. StatPearls and GeneReviews are ND: link only.
- Hosted Postgres is not reachable from the sandbox. Use the Space's admin seed job and the Management API.
