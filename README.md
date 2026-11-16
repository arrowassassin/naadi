# Agent ledger

Append-only coordination log for parallel agents. **Never edit or delete files here**; only add.
Use the CLI: `python .claude/skills/multiplayer/scripts/multiplayer.py --help`.

- `messages/YYYY/MM/DD/<id>.json` — one message per file (claim, contract, decision, question, answer, block,
  unblock, conflict, handoff, status, review-request, done, nudge, note, hello, release)
- `acks/<message-id>/<agent>.json` — one acknowledgement per agent per message
- `agents/<name>.json` — who is working, on what, where

- tasks: `task` messages from the lead; state comes from accept/update/block/done/verify replies

Shared code goes to the shared commit branch `claude/naadi-shared`; the lead promotes it after review.
