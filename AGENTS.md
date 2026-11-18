# AGENTS.md (read by opencode, Codex CLI and most other coding agents)

Naadi: iPad-first USMLE Step 1 + Step 2 CK study platform. Start here:

1. `handoff/NEXT_AGENT.md`: full handover (what is live, what is left, how to run, deploy and seed). Read it first.
2. `CLAUDE.md` and `handoff/HANDOFF.md`: working agreement and the product/engineering spec.
3. `docs/REVIEW_CHECKLIST.md`: what every change must satisfy before it counts as done.

Hard rules: free models only (`config/models.yaml`, calls only through `apps/api/naadi_api/llm.py`); no copyrighted
content and every asset cites a `sources` row; never reveal an answer or explanation before the student answers;
DB changes only via Alembic with RLS in the same migration; every UI string is a `ui_copy` key; never commit or
print secrets; never run tests against the hosted database (export loopback `DATABASE_URL`, `SUPABASE_URL`,
`APP_BASE_URL`); work on `claude/blissful-knuth-1gh22d`, never force-push, no PRs or merges to `main` unless the owner asks.
