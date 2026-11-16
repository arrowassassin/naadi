# Progress

## Done
- **Env setup (§0.5):** `.env.example`, `docs/SECRETS.md`, `scripts/check_env.py` (official SDKs; 46 pass, 0 required fail), `scripts/push_space_secrets.sh`, `docs/DEVICE_CHECKLIST.md`. Space `arrowassassin/naadi` has 5 secrets + 12 variables.
- **Plan:** `docs/PLAN.md` (22 segments, 7 whole-app reviews + final audit).

## In progress
- Segment 01 — M0 scaffold, tokens, contracts, health, CI, Docker Space deploy (`implementer-opus`).

## Next
- Segment 02 — Alembic migrations + RLS + storage policies.

## Decisions
| Date | Decision |
|---|---|
| 2026-11-16 | Docker Space on free `cpu-basic` (account is PRO), private for now |
| 2026-11-16 | Supabase Storage only; no R2 for now |
| 2026-11-16 | Sign-in: Google OAuth + magic link (no Apple) |
| 2026-11-16 | Official SDKs only (supabase, openrouter, tavily-python, huggingface_hub, langgraph); raw HTTPS only where no SDK method exists |
| 2026-11-16 | Pydantic → OpenAPI → generated zod + TS client |
| 2026-11-16 | DB-backed tests run in GitHub Actions + on the Space (sandbox has no raw TCP) |
| 2026-11-16 | Bundle dirs moved from `handoff/` to the repo root |

## Risks
- GitHub Actions secrets must be added by the owner (this session can't write them).
- OpenRouter daily cap is 50 until the one-time $10 purchase.
- Private Space blocks PWA install on iPad; decide public vs custom domain before M5.
- Supabase region is us-west-2 (HANDOFF suggested Mumbai); fine, the Space runs in the US.
