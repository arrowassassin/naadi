# Progress

## Done

- **Env setup (§0.5):** `.env.example`, `docs/SECRETS.md`, `scripts/check_env.py` (official SDKs; 46 pass, 0 required fail), `scripts/push_space_secrets.sh`, `docs/DEVICE_CHECKLIST.md`. Space `arrowassassin/naadi` has 5 secrets + 12 variables.
- **Plan:** `docs/PLAN.md` (22 segments, 7 whole-app reviews + final audit).

## Approved
- **S02 — DB migrations, RLS, storage** (2026-11-16, reviewer-opus re-review APPROVED at `c8ef1b7`): 33 tables, RLS on 17 user tables, answer columns hidden, embeddings server-only, no self-grading, attempt-gated question-linked flashcards, 5 buckets, default privileges closed; 132 db + 183 unit tests; 20 reviewer mutations caught. Real-project migration runs on the Wave A deploy.
- **S01 — M0 scaffold, tokens, contracts, health, CI, Docker Space deploy** (2026-11-16, re-review APPROVED at `4ab3308`): deploy lives in its own `.github/workflows/deploy.yml` (ci.yml holds no secrets and never deploys), `scripts/scan_artifacts.py` guards uploaded artifacts, canonical frozen-DOM axe helper in `apps/web/e2e/fixtures.ts`, JSON uvicorn logging config, log redaction, version from `naadi_api/__version__`. Merged into the shared branch.

## In progress

- **S12a — ink engine** (seg12-ink, review rework): `@naadi/ink` + `/system/ink-lab` (server flag: /api/me
  `features.ink_lab` when signed in, /api/public-config when signed out; admin-only in production).
  Re-recorded 2026-11-16 after the latency fix (latency is now measured from the first input not yet painted,
  so it includes the wait for the frame). Headless Playwright, software rendering, synthetic pen events at 4 ms,
  local sandbox under shared load (load average about 22 on 4 cores), dpr 1 unless noted:
  - input-to-paint p50 / p95: Chromium 10.5–12.8 / 18–62 ms; Firefox 14–15 / 22–38 ms; WebKit 12–15 / 37–65 ms;
    WebKit iPad portrait at dpr 2: 36 / 132 ms (before the live canvas was sized to the stroke: 107 / 513 ms).
  - Settling ink into images costs 0.5–9 ms of main-thread time per page; the PNG encode runs in a worker.
  - 5,000-stroke page, rAF-driven scroll, best of 4 paired runs (fps with ink / with the ink hidden): Chromium
    45–60 / 50–60 (ratio 0.89–0.99); Firefox 52–59 / 56–58 (0.91–1.01); WebKit phones 49 / 53–56, iPad 37–39 / 41–42,
    laptop 29 / 33, desktop 17 / 24, iPad dpr 2: 11 / 15 (ratios 0.73–0.95). The runner cannot scroll the large WebKit
    pages at 60 fps with no ink at all, so the e2e gate is ink >= 0.7 x ink-hidden, plus >= 25 fps when the
    ink-hidden run reaches 30 fps.
  - Canvas memory: one page-wide budget of 24 Mpx. Twelve surfaces at dpr 2 peak at 22.9 Mpx with zero evictions;
    settled pages hold no canvas memory at all.
  **Capacitor/PencilKit decision: not yet.** Stay on the web engine; decide from the owner's 240 fps slow-motion
  run on the iPad (lab → Slow-motion mode; `docs/DEVICE_CHECKLIST.md`). Rule: if the median tip-to-ink time with
  predicted ink on is above 20 ms (more than ~5 frames at 240 fps), plan Capacitor + PencilKit for the ink
  surfaces only. Record device, iPadOS version, median of 10 strokes with prediction on and off here.

## Next

- Segment 02 — Alembic migrations + RLS + storage policies (`naadi_alembic_version`).
- Segment 03 — Auth + app shell.
- Segment 04 — Agent platform (brief written: `docs/segments/04-agent-platform.md`).

## Cost and leak audit (2026-11-16)

| Service        | Finding                                                                                                                                                                                          | Can it charge?                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| OpenRouter     | Key credit limit **$0**, usage $0, `is_free_tier=true`; all 8 configured model IDs exist (catalog: 433 models, 14 free). A free call reached the provider (upstream 429, not a 402 credit block) | No: the $0 key limit blocks every paid call; code also rejects non-`:free` IDs |
| Tavily         | Free "Researcher" plan, 1,000 credits/month, pay-as-you-go **off**                                                                                                                               | No                                                                             |
| Supabase       | Free plan, project `ACTIVE_HEALTHY`, us-west-2                                                                                                                                                   | No: Free has no overage billing                                                |
| Hugging Face   | Space `naadi` private; moving from ZeroGPU to free `cpu-basic`                                                                                                                                   | No: `cpu-basic` is free; never request paid hardware                           |
| GitHub Actions | Repo is **public** → unlimited free minutes on ubuntu runners; only secret is `HF_TOKEN`                                                                                                         | No: no macOS or larger runners                                                 |
| Leaks          | Public repo: `.env*` gitignored (except `.env.example`), scripts never print values, Space secrets set by name from env (never on argv), `HF_TOKEN`/`SUPABASE_ACCESS_TOKEN` kept off the Space   | —                                                                              |

## Decisions
| 2026-11-16 | Promotion is per wave: the shared branch carries concurrent tasks, so the lead promotes + deploys once every task in a wave is APPROVED (then runs real-project checks: S02 live_check, S03 live auth, S05 seed) |
| 2026-11-16 | Deferred to after completion (owner): fine-grained `HF_GEN_TOKEN` for the app (15b is built and verified with the build session's token; app shows generation "not configured" until set) and making the Space public (18 is tested against the private Space). Neither blocks end-to-end completion. YOUTUBE_API_KEY added (env + Space). |

| Date       | Decision                                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-11-16 | Docker Space on free `cpu-basic` (account is PRO), private for now                                                                                      |
| 2026-11-16 | Supabase Storage only; no R2 for now                                                                                                                    |
| 2026-11-16 | Sign-in: Google OAuth + magic link (no Apple)                                                                                                           |
| 2026-11-16 | Official SDKs only (supabase, openrouter, tavily-python, huggingface_hub, langgraph); raw HTTPS only where no SDK method exists                         |
| 2026-11-16 | Pydantic → OpenAPI → generated zod + TS client                                                                                                          |
| 2026-11-16 | DB-backed tests run in GitHub Actions + on the Space (sandbox has no raw TCP)                                                                           |
| 2026-11-16 | Agent platform: Aegra (Apache-2.0, LangGraph-SDK compatible) hosts every agent graph and our FastAPI app in one container; pulled forward to segment 04 |
| 2026-11-16 | CI needs only `HF_TOKEN`; DB tests use a `supabase/postgres` 17.11 service container; migrations apply on Space start                                   |
| 2026-11-16 | Bundle dirs moved from `handoff/` to the repo root                                                                                                      |

## Deferrals

- **Course map filters "Weak areas only" and "High-yield only"** (design/Courses.dc.html), deferred from S06.
  "Weak areas only" needs S11's mastery and weak-spot model, so it lands with S11. "High-yield only" needs a
  per-chapter or per-section high-yield flag in the content schema and seed, which does not exist yet. S06 ships
  neither control, so the map has no dead buttons; the exam and system filters cover browsing until then.

## Risks
- `multiplayer.py integrate` rebases, so it can't land a branch merge (e.g. promoting dev back into shared); lead-merge used a plain fast-forward push. Add an `integrate --merge` mode to the skill before the next wave-end promotion.


- GitHub Actions has one secret, `HF_TOKEN` (added by the owner). DB tests in CI use the `supabase/postgres` image; real-project tests run on the Space.
- OpenRouter daily cap is 50 until the one-time $10 purchase.
- Private Space blocks PWA install on iPad; decide public vs custom domain before M5.
- Aegra is pre-1.0; pinned, with an in-process `langgraph` fallback if it blocks us.
- Supabase region is us-west-2 (HANDOFF suggested Mumbai); fine, the Space runs in the US.
