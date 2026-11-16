# Progress

## Done

- **Env setup (§0.5):** `.env.example`, `docs/SECRETS.md`, `scripts/check_env.py` (official SDKs; 46 pass, 0 required fail), `scripts/push_space_secrets.sh`, `docs/DEVICE_CHECKLIST.md`. Space `arrowassassin/naadi` has 5 secrets + 12 variables.
- **Plan:** `docs/PLAN.md` (22 segments, 7 whole-app reviews + final audit).

## In progress

- Segment 01 — M0 scaffold, tokens, contracts, health, CI, Docker Space deploy (`implementer-opus`).

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

- GitHub Actions has one secret, `HF_TOKEN` (added by the owner). DB tests in CI use the `supabase/postgres` image; real-project tests run on the Space.
- OpenRouter daily cap is 50 until the one-time $10 purchase.
- Private Space blocks PWA install on iPad; decide public vs custom domain before M5.
- Aegra is pre-1.0; pinned, with an in-process `langgraph` fallback if it blocks us.
- Supabase region is us-west-2 (HANDOFF suggested Mumbai); fine, the Space runs in the US.
