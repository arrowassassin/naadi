---
name: multiplayer
description: Run several coding agents in parallel on one repo without stepping on each other. A lead agent distributes tasks on a git "ledger" branch (task board + message bus), each agent accepts its task, posts updates, asks/answers questions, acknowledges messages, claims files, and pushes its commits to ONE shared branch; the lead verifies and promotes the shared branch after review. Use this skill whenever work is being split across subagents, teammates or parallel sessions, whenever you are about to spawn more than one implementer at once, when an agent needs something from another agent (an interface, a schema, an answer, a file someone else is editing), when you hit a merge conflict on a shared branch, or when you are a subagent that was told it is part of a multiplayer/parallel wave — even if the word "multiplayer" isn't used.
---

# Multiplayer

Parallel agents fail in predictable ways: two of them edit the same file, one waits silently for something the other never knew it needed, decisions live in someone's context window and vanish, and pushes race. Multiplayer fixes this with two git branches and one small CLI:

- **Ledger branch** — the task distributor and message bus. Append-only: every task, update, question, answer, decision, claim, conflict and acknowledgement is a new file. It is the shared memory of the run and its audit trail.
- **Shared branch** — the one branch every parallel agent commits to, through `integrate` (rebase on the latest tip, test, push, retry; conflicts are posted to the ledger automatically).
- **Promote branch** — where the lead merges the shared branch after review. Agents never push there.

Branch names and the lead's name live in `.claude/multiplayer.toml`:

```toml
[multiplayer]
remote        = "origin"
ledger_branch = "claude/naadi-ledger"
shared_branch = "claude/naadi-shared"
promote_to    = "claude/blissful-knuth-1gh22d"
lead          = "lead"
```

Everything goes through the CLI (standard-library Python 3.11+, no checkout of the ledger branch needed, safe to run from any worktree at the same time):

```bash
MP="python .claude/skills/multiplayer/scripts/multiplayer.py"
$MP --help            # every command; `$MP <cmd> -h` for options
```

Use the CLI rather than writing ledger files by hand — it guarantees ids, timestamps, threading, acks and race-free pushes.

## Roles

**Lead** (the agent that splits the work; usually the main session):
1. **Decide the split.** Group work into waves of tasks that can proceed independently; note where tasks touch (shared files, interfaces, schemas). A task is the unit one agent owns end to end.
2. `$MP init`, `$MP hello --as lead --role lead`.
3. **Post the plan** so every agent sees the whole picture, not just its slice:
   `$MP plan --as lead --subject "Wave A" --body-file plan.md` (owners, dependencies, interfaces to agree, files each task owns).
4. **Assign one task per agent:**
   `$MP assign --as lead --to seg02-db --key S02 --subject "DB migrations + RLS" --acceptance "migrations + RLS tests green in CI" --paths "apps/api/migrations/**" --deps S01 --body-file brief.md`
5. **Spawn the agents** (in their own worktrees, e.g. the Agent tool with `isolation: "worktree"`), telling each: its name, its task key, the names of the other agents in the wave, and to follow this skill. Keep the spawn prompt short — the task and plan live on the ledger.
6. **Watch the board**, not the transcripts: `$MP tasks`, `$MP board`, `$MP inbox --as lead`. Answer questions and decide contracts quickly — a waiting agent is wasted capacity. Unblock or re-plan when something stalls.
7. **Verify** each finished task (run tests, review), then `$MP verify --as lead S02` or `$MP verify --as lead S02 --reopen --subject "why"`.
8. **Promote** after review: `$MP promote --as lead --to <promote_to>`.

**Agent** (an implementer in the wave):
1. `$MP hello --as <name> --segment S02 --purpose "DB migrations"`.
2. Read your task and the plan: `$MP inbox --as <name>`, `$MP show <task>`; then `$MP accept --as <name> S02` (or `decline` with a reason if it's wrong for you).
3. Base your work on the shared branch: `git fetch origin <shared_branch> && git switch -c work/<name> origin/<shared_branch>`.
4. **Claim before you edit shared files.** `$MP claim --as <name> --paths "packages/shared/**" --reason "adding Profile schema" --ttl 90`. Exit code 3 means someone else holds it: message them instead of editing. Release when done (`$MP release`). Files only you touch (inside your task's `--paths`) don't need extra claims.
5. **Check your inbox at every natural checkpoint** (after each commit, before starting a sub-step, before declaring done) and **acknowledge** what you read: `$MP ack --as <name> <id>`. Replying with `--re` acknowledges automatically.
6. **Talk instead of guessing.** Agree interfaces with a `contract` the other side `decision`s; ask with `question`, answer with `answer --re`; if you can't proceed, post `block` naming exactly what you need, then do other useful work or `$MP wait --as <name> --type answer,decision,unblock --re <id>`.
7. **Commit small, integrate often:** `$MP integrate --as <name> --test-cmd "<fast tests>" --message "what landed"`. Exit 4 = conflict, already posted to the ledger with the files and the commits involved; coordinate with the owner, resolve on your branch, integrate again. Never force-push the shared branch.
8. Post `update` on your task when something meaningful changes (not every minute): what's done, what's next, risks.
9. When the acceptance criteria are met: `$MP done --as <name> S02 --commit <sha> --subject "what's done, how verified"`. Stay available for a `reopen`.

## Message types

| Type | Who | Use it for | Reply expected |
|---|---|---|---|
| `plan` | lead | The wave: tasks, owners, dependencies, interfaces | ack |
| `task` | lead → agent | One owned unit of work with acceptance criteria and paths | `accept` / `decline` |
| `accept` / `decline` | agent | Take the task, or refuse with a reason | — |
| `update` | agent | Progress on a task | — |
| `claim` / `release` | any | Advisory lock on paths (TTL, default 120 min) | — |
| `contract` | any | Proposed interface (schema, API, props, file layout) | `decision` |
| `decision` | owner/lead | Accept/amend a contract or settle a question for everyone | ack |
| `question` / `answer` | any | Ask for a requirement or fact; answer with `--re` | `answer` |
| `block` / `unblock` | any | "I'm stopped until X" / "X is ready" | `unblock` |
| `conflict` | any (auto from `integrate`) | Overlapping edits on the shared branch | coordination, then `note`/`unblock` |
| `handoff` | any | Passing remaining work or context to another agent | ack |
| `review-request` | agent → lead | Ask for a review before `done` | `verify`/`reopen` |
| `done` | agent | Task complete (with commit refs) | `verify` / `reopen` |
| `verify` / `reopen` | lead | Accept the task, or send it back with the reason | — |
| `status`, `note`, `nudge`, `hello` | any | FYI; `integrate`/`promote` post `status` automatically | — |

Details of the file layout, message fields and exit codes: `references/protocol.md`.

## Good habits (and why)

- **The ledger is the source of truth; direct messages are doorbells.** If your environment lets agents message each other directly (e.g. `SendMessage`), use it only to say "ledger msg `<id>` needs you" — the content lives on the ledger, where everyone (and a later reviewer) can see it.
- **Write messages that stand alone.** The reader may be a fresh agent with no context: say what you need, why, by when, and link files/commits. One topic per message; reply in the thread with `--re`.
- **Acknowledge promptly, even if you can't act yet** (`ack --note "will do after S02"`). An unacked message is indistinguishable from a lost one; `board` shows who hasn't acked.
- **Prefer agreeing a contract early to resolving a conflict late.** If two tasks meet at an interface, post the contract before writing code on either side.
- **Keep the shared branch green.** Integrate only what passes your fast tests; a broken shared branch stalls everyone. If you break it, say so on the ledger immediately and fix or revert.
- **Never rewrite history on the ledger or shared branch** and never edit someone else's message — post a correction that references it.
- **Keep scratch files inside your own worktree** (e.g. a gitignored `.tmp/`), never in a directory other agents share: parallel agents in one session share the same scratchpad and temp paths, and a colliding file name silently corrupts another agent's run.
- **Secrets never go on the ledger** (it is a normal git branch and may be public). Reference where a value lives, never the value.
- **Before stopping for any reason** (done, blocked, out of budget), commit, integrate what's ready, and post an `update`/`block`/`handoff` so nobody waits on a silent agent.

## Verifying the tool

`bash .claude/skills/multiplayer/tests/test_multiplayer.sh` runs a lead and two agents against a throwaway remote: task flow, 12 concurrent posts with no losses, question/answer/ack, claims (exit 3), shared-branch races and conflicts (exit 4, auto-posted), done/verify and the board.
