# Progress

## Done

- **Env setup (§0.5):** `.env.example`, `docs/SECRETS.md`, `scripts/check_env.py` (official SDKs; 46 pass, 0 required fail), `scripts/push_space_secrets.sh`, `docs/DEVICE_CHECKLIST.md`. Space `arrowassassin/naadi` has 5 secrets + 12 variables.
- **Plan:** `docs/PLAN.md` (22 segments, 7 whole-app reviews + final audit).

## Approved
- **S02 — DB migrations, RLS, storage** (2026-11-16, reviewer-opus re-review APPROVED at `c8ef1b7`): 33 tables, RLS on 17 user tables, answer columns hidden, embeddings server-only, no self-grading, attempt-gated question-linked flashcards, 5 buckets, default privileges closed; 132 db + 183 unit tests; 20 reviewer mutations caught. Real-project migration runs on the Wave A deploy.
- **S01 — M0 scaffold, tokens, contracts, health, CI, Docker Space deploy** (2026-11-16, re-review APPROVED at `4ab3308`): deploy lives in its own `.github/workflows/deploy.yml` (ci.yml holds no secrets and never deploys), `scripts/scan_artifacts.py` guards uploaded artifacts, canonical frozen-DOM axe helper in `apps/web/e2e/fixtures.ts`, JSON uvicorn logging config, log redaction, version from `naadi_api/__version__`. Merged into the shared branch.

## In progress

- **S12a — ink engine** (seg12-ink, in review): `@naadi/ink` + `/system/ink-lab` (server flag `features.ink_lab`).
  Measured on CI (GitHub `ubuntu-24.04`, headless, software rendering, synthetic pen events at 4 ms):
  input-to-paint p50 2.4–3.2 ms / p95 4.3–7 ms; input-to-next-frame p50 16.3–19.1 ms / p95 20.5–46.5 ms
  (Chromium, six viewports). 5,000-stroke page, rAF-driven scroll, best of paired runs (ink shown / hidden):
  phones 59.7–60 / 59.3–59.7 fps; iPad portrait 49.7 / 56.4; iPad landscape 49.4 / 55.7; laptop 50.7 / 55.4;
  desktop 39.5 / 45.4 fps. The runner itself does not reach 60 fps on large viewports with the ink hidden,
  so the e2e gate is min(55 fps, 70 % of the ink-hidden run). WebKit (CI, best of 3, shown / hidden): phones 56.1 / 59.3, iPad portrait 41.3 / 50.2, iPad landscape 37.5 / 47.5, laptop 27.5 / 39.6, desktop 28.2 / 31.9; Firefox 54–59 with ink, within 2 fps of hidden. WebKit input-to-paint p50 4–5 ms / p95 8–9 ms; Firefox 3–4 / 6–10 ms.
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

## Risks
- `multiplayer.py integrate` rebases, so it can't land a branch merge (e.g. promoting dev back into shared); lead-merge used a plain fast-forward push. Add an `integrate --merge` mode to the skill before the next wave-end promotion.


- GitHub Actions has one secret, `HF_TOKEN` (added by the owner). DB tests in CI use the `supabase/postgres` image; real-project tests run on the Space.
- OpenRouter daily cap is 50 until the one-time $10 purchase.
- Private Space blocks PWA install on iPad; decide public vs custom domain before M5.
- Aegra is pre-1.0; pinned, with an in-process `langgraph` fallback if it blocks us.
- Supabase region is us-west-2 (HANDOFF suggested Mumbai); fine, the Space runs in the US.
