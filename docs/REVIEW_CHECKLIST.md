# Pre-review checklist (run before posting `done`)

These are the defects that sent almost every segment back after its first review. Check each one, and list the result in your done message.

## Copy and UI
- [ ] No user-facing string in JSX or server responses: every string is a `ui_copy` key in `config/app_config.yaml` and in `REQUIRED_UI_COPY_KEYS`. Shipped defaults are regenerated, and your source root is added to `apps/web/src/content/noHardcodedCopy.test.ts`.
- [ ] Server responses carry structured fields, never English sentences. The client formats them with `ui_copy`.
- [ ] One lime CTA per view. Status is never shown by colour alone (a word or icon goes with it).
- [ ] Phone 390/412: nothing truncated or overlapping (tab bar labels, pills, toolbars). Nothing is hidden under the floating nav pill or the bottom bars (scroll-padding).
- [ ] No dead links or buttons. A feature that isn't built yet is absent, or disabled with a visible reason.
- [ ] Keyboard shortcuts don't fire inside inputs, dialogs, scroll regions or media.

## Security, leaks, privacy
- [ ] No answer, explanation, why-not or objective reaches the client before an attempt, through any endpoint, snippet, cache or offline store. That includes mock exam content and answer-key tables.
- [ ] Clients cannot write server-authoritative columns or tables. RLS and grants go in the same migration, the RLS coverage test and the live_check list are updated, and there is a single alembic head (claim the revision number on the ledger).
- [ ] No inline styles or scripts that break the production CSP. Run at least one e2e against the `APP_ENV=prod` server with zero console errors.
- [ ] Queries and user text are scrubbed before any LLM or web call, and never logged in plain text.
- [ ] Only free models and services. LLM calls go only through `naadi_api/llm.py`.

## Tests
- [ ] Unit, db (fresh stack), vitest, lint (biome + ruff), typecheck, `pnpm gen:contracts` and `pnpm gen:requirements` all pass with no drift.
- [ ] Local e2e: the smoke set below passes 2× in a row with `--retries=0`. The full 3 browsers × 6 viewports matrix is CI's job. Link the CI run in your done message.
- [ ] e2e tests wait for in-flight requests and fonts before going offline or navigating (no races).
- [ ] Screenshots of every state at iPad portrait/landscape and phone, compared with the board.

## Local e2e smoke set
```bash
pnpm --filter @naadi/web exec playwright test <your specs> --retries=0 --workers=2 --project webkit-ipad-portrait --project webkit-ipad-landscape --project webkit-phone-390 --project chromium-laptop-1440
```
Reviewers use the same set locally and judge the full matrix from CI.

## Sandbox hygiene
- Use your own E2E_PORT block, and only your own Postgres, Storage and auth ports and container suffix.
- Never `pkill` or `docker rm` by a generic pattern or by port. Match your worktree path or your container suffix.
- Temp files stay inside your worktree (`.tmp/`).
