# Multiplayer protocol reference

## Ledger branch layout (append-only)

```
README.md
agents/<name>.json                         # registry: role, segment, worktree, purpose, started
messages/YYYY/MM/DD/<id>.json              # one message per file
acks/<message-id>/<agent>.json             # one acknowledgement per agent per message
```

Writers only add files, so concurrent writes never conflict. The CLI never checks the branch out: it fetches the tip into a private ref (`refs/multiplayer/<agent>-<pid>`), builds a commit with a temporary index (`read-tree` → `hash-object`/`update-index` → `write-tree` → `commit-tree`) and pushes it; a rejected push is rebuilt on the new tip and retried with jittered backoff.

## Message fields

| Field | Meaning |
|---|---|
| `id` | `YYYYMMDDTHHMMSS-<from>-<6 hex>`; any unique prefix works in the CLI |
| `ts` | UTC ISO-8601 with microseconds |
| `from`, `to` | agent names; `to` may be `["all"]` |
| `type` | see SKILL.md table |
| `subject`, `body` | one-line summary, markdown body |
| `re` | parent message id (replies, task updates, releases) |
| `thread` | root id of the conversation (set automatically from `re`) |
| `task_key` | short key on `task` messages (`S02`); the CLI accepts key or id |
| `paths` | glob list for claims, tasks and conflicts |
| `deps` | task ids this task waits on |
| `acceptance` | how the lead will judge `done` |
| `ttl_minutes` | claim lifetime (default 120) |
| `refs` | `{commit, branch, pr}` |
| `requires_ack` | whether recipients are expected to acknowledge |

## Task states (derived, never stored)

`assigned → accepted → in_progress ⇄ blocked → done → verified` (or `reopened` → back to work, or `declined`). The state is computed from the replies (`re` = task id): `accept`, `update`, `block`/`unblock`, `done`, `verify`, `reopen`, `decline`.

## Claims

Active = a `claim` without a matching `release` whose TTL hasn't expired. Overlap is checked with glob matching both ways and directory prefixes (`packages/shared/**` overlaps `packages/shared/src/x.ts`). Claims are advisory: they prevent accidental collisions, not malice.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | not found (message/task) |
| 2 | git or network failure after retries |
| 3 | `claim`: overlapping active claim by another agent |
| 4 | `integrate`: rebase conflict (posted to the ledger; your branch is unchanged) |
| 5 | `wait`: timeout |

## Environment overrides

`MP_REMOTE`, `MP_LEDGER_BRANCH`, `MP_SHARED_BRANCH` override `.claude/multiplayer.toml`; command-line flags override both.
