# Wave A (starts when segment 01 is APPROVED and promoted)

| Task | Agent | Owns (paths) | Depends on | Brief |
|---|---|---|---|---|
| S02 DB migrations, RLS, storage policies | seg02-db | apps/api/migrations/**, db/schema.sql, apps/api/tests/db/** | S01 | docs/segments/02-db-migrations-rls.md |
| S03 Auth, admin role, app shell | seg03-auth | apps/api/naadi_api/auth*, apps/web/src/auth/**, apps/web/src/shell/**, apps/web/src/routes/** | S01; profiles from S02 | docs/segments/03-auth-shell.md |
| S12a Ink engine (no persistence) | seg12-ink | packages/ink/**, apps/web/src/ink/** | S01 | docs/segments/12a-ink-engine.md |

Agree early with a contract:
- seg02-db and seg03-auth: `profiles` columns, the `auth.users` FK and the RLS test helpers.
- seg03-auth and seg12-ink: the shell content slot and pen-surface registration.

Claim before editing shared files: `packages/shared/**`, `packages/ui/**`, `apps/web/src/main.tsx`, `apps/api/naadi_api/main.py`, `package.json`, `pyproject.toml`, `.github/**`.

Commit to `claude/naadi-shared` via `integrate` only. The lead reviews each task with reviewer-opus, then promotes to `claude/blissful-knuth-1gh22d`.
