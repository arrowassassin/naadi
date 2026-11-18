# Start a new coding agent on Naadi (opencode, Codex CLI, Gemini CLI, Aider, or any other)

## 1. Get the code and tools (5 minutes)

```bash
git clone https://github.com/arrowassassin/naadi && cd naadi
git checkout claude/blissful-knuth-1gh22d          # the deployed branch; never force-push, no PRs unless asked
# tools: Node 22 + pnpm, uv (Python), Docker, git, gh (optional)
pnpm install --frozen-lockfile && (cd apps/api && uv sync --frozen)
```

Set the secrets as **environment variables** (never in files or chat). The names are in `handoff/NEXT_AGENT.md`
section 7; `uv run scripts/check_env.py` checks them without printing values. For tests, export loopback
`DATABASE_URL`, `SUPABASE_URL` and `APP_BASE_URL` (section 6): the real values point at the live project.

## 2. Start the CLI in the repo root and paste this prompt

```text
You are taking over the Naadi build (USMLE study app). Read, in order: AGENTS.md, handoff/NEXT_AGENT.md (the full
handover), CLAUDE.md, docs/REVIEW_CHECKLIST.md. Work on branch claude/blissful-knuth-1gh22d. Follow section 9 of
NEXT_AGENT.md in order: get a real test baseline first, then fix and verify, then continue the content and hardening
work. Obey the hard rules (free models only, no copyrighted content, never reveal answers early, RLS in every
migration, no secrets in files or logs, never touch the hosted database from tests, never force-push, no PRs unless
I ask). Commit small and often with clear messages, push to the branch, and after each milestone give me a 5-line
status: what changed, what is verified, what is left. Ask me only for decisions listed in NEXT_AGENT.md section 8.
Start now with step 1 (orient) and step 2 (baseline).
```

## 3. Tips

- **opencode:** `opencode` in the repo root reads `AGENTS.md` automatically. Pick the strongest model you have; use a
  *different* model (or a human clinician) than the one that wrote the content for medical review.
- **Context is large.** Let the agent read files on demand; do not paste whole files. The ledger of everything the
  previous agents decided is on branch `claude/naadi-ledger` (`python3 .claude/skills/multiplayer/scripts/multiplayer.py tasks`).
- **One piece at a time.** The most valuable first hours: green CI baseline, the live-auth token-leak fix, then
  independent medical review of the content already live (2,019 new questions are only self-reviewed).
- **Claude cloud sessions** that stopped at the weekly limit can also be resumed after 2026-11-23 09:00 UTC from the
  claude.ai Code session list; they hold unfinished content work (also backed up on the `work/seg23-*` branches).
