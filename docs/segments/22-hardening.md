# Segment 22 — Ops: backups + tested restore, rate-limit audit, strict CSP, ASVS L2, performance, docs, SearXNG

**Implementer:** `implementer-opus` (PLAN: security + recovery). **Reviewer:** `reviewer-opus`, followed by the final full-app audit. Follow `docs/REVIEW_CHECKLIST.md` and list each result in the done message.
**Wave:** H (alone; needs every earlier segment merged). Claim the next free Alembic revision if a migration is needed (pruning functions only). Own cloud session: no fixed local ports (probe free ports; containers suffixed `-seg22`).
**Depends on:** all segments 01–21; in particular S01 (security headers, CSP, CI), S02 (RLS suite, `live_check.py`), S04 (`search.py` `_searxng`/`parse_searxng`, `search.layers.searxng`), S16/S18 (manifest + service worker), `.github/workflows/cron.yml` (daily keep-alive; "backups arrive in segment 22").
**HANDOFF refs:** §2.1 mitigations 2–3 (nightly `pg_dump` → private HF dataset, 30-day retention; prune checkpoints > 30 days, `llm_usage` > 90 days), §10 CI crons, §12 quality bar, §13 risks 9–11. `docs/MODEL_TESTS.md` → SearXNG (how to enable). `docs/RUNBOOK.md`, `docs/SECRETS.md`, `docs/DEVICE_CHECKLIST.md`. Boards: `design/Main.dc.html` + `design/PhoneToday.dc.html` (performance target screen), `design/System.dc.html` (degradation/health states).

## Scope
### Backups and restore
- Backup job (lead decision: runs on the Space as an Aegra system cron at 03:00 UTC, not in GitHub Actions, so it needs no new GitHub secret and no Actions minutes): `pg_dump --format=custom --no-owner` of `public` and Aegra's tables (excluding `search_cache` and checkpoints older than 30 days), encrypted with `age` to the owner's public key (`BACKUP_AGE_RECIPIENT`, a Space variable; the private key never leaves the owner), pushed with `huggingface_hub` using a fine-grained write token scoped to that one dataset (`HF_BACKUP_TOKEN`, Space secret — owner decision, deferred together with `HF_GEN_TOKEN`; until set the job reports "not configured" on the admin health panel) to a **private** dataset `arrowassassin/naadi-backups` under `db/YYYY-MM-DD.dump.age`; retention 30 days (older files deleted in the same job).
- Storage: weekly encrypted tarball of `user-uploads`, `vault` and `generated-media` plus the `content-public`/`content-private` objects the seed did not create (admin uploads) to the same dataset under `storage/`.
- **Restore** `scripts/restore.py --date` (decrypt, `pg_restore` into a target URL, Storage re-upload) tested in CI weekly against a fresh `supabase/postgres` service container: restore → `alembic current` = head → `live_check.py` row counts and RLS checks pass. Tested once by hand into a scratch Supabase project (the free plan allows a second project), steps recorded in RUNBOOK.
- Pruning: daily job deletes `llm_usage` > 90 days, `search_cache` past TTL, `sync_ops` > 24 h, tombstones > 90 days; Aegra checkpoint TTL confirmed in `aegra.json`.
### Security
- **Rate-limit audit**: a test enumerates every route in the OpenAPI document and asserts a per-user (or per-IP for anon/ICS) limit is attached; heavy routes (ingest, export, generation, admin writes, auth) have stricter ones. Results table in SECURITY.md.
- **CSP strict**: no `unsafe-inline`/`unsafe-eval`; `script-src 'self'`; `style-src 'self'` (Streamdown/KaTeX/Shiki styles bundled; Mermaid rendered with `htmlLabels: false` or into a sandboxed SVG); `frame-src https://www.youtube-nocookie.com https://www.youtube.com`; `connect-src 'self'` + Supabase project host; `img-src` own + Supabase Storage + YouTube thumbnails + PMC bucket; `worker-src 'self'`; `frame-ancestors 'none'`; `require-trusted-types-for 'script'` in report-only with a reporting endpoint that rate-limits and never logs bodies. One e2e per main route under `APP_ENV=prod` with zero CSP violations.
- **Dependency audit in CI**: `pnpm audit --prod --audit-level high` and `uv run pip-audit` (pin) on every push; GitHub Actions pinned by SHA (already) checked by a test; SBOM (`cyclonedx`) uploaded as an artifact.
- **ASVS 4.0.3 Level 2** checklist in `docs/SECURITY.md`: each requirement → met (with file/test link), not applicable (reason) or gap (issue + owner). Covers auth/session (Supabase JWT via JWKS), access control (RLS suite), input validation (pydantic everywhere), file handling (S15 caps), secrets, logging (no bodies), CSP/headers, SSRF (only allowlisted hosts in `search.py`, `pathway_verify`, `youtube_map`), crypto (ICS tokens, signed URLs).
### Performance and PWA
- Lighthouse CI (`@lhci/cli`, pin) on `/`, `/learn/…`, `/practice`, `/plan` with an iPad profile (820×1180, Slow 4G, 4× CPU): performance ≥ 90, accessibility 100, best practices ≥ 90. Lighthouse dropped its PWA category in v12, so **installability** is asserted instead: Chromium `Page.getInstallabilityErrors` empty, manifest fields complete, SW controls the page, offline reload works.
- **Today interactive < 2 s** on that throttled profile (TTI from a trace, warm SW, cold data). Budgets enforced in CI (`size-limit`, pin): initial JS ≤ 250 KB gzip, CSS ≤ 40 KB, each route chunk ≤ 150 KB; Mermaid, Shiki, KaTeX, pdf.js, Excalidraw, TipTap editor and admin lazy-loaded. API p95 targets from earlier segments rechecked on the Space (`/api/today` < 1.5 s warm, local search < 300 ms).
### SearXNG (search layer 8, deferred from S04)
- Run SearXNG in the Space container on `127.0.0.1:8888` (granian, pinned release, `settings.yml` with `formats: [html, json]`, limiter on, engines limited to general web + Wikipedia, no image engines), started by the container entrypoint before Aegra and health-checked in `/api/health`. Enable through `app_config.search.layers.searxng.enabled` (admin editor, no redeploy) only after `tests/agent/test_search_unit.py` and the search integration tests pass against it; memory budget checked on `cpu-basic`.
### Docs (written, reviewed, linked from README)
- `docs/RUNBOOK.md` (update: deploy, rollback, restore, rotate secrets, quota exhaustion, Supabase pause, Space sleep, SearXNG), `docs/ARCHITECTURE.md` (diagram, processes, data flow, agent graphs, budgets), `docs/CONTENT_GUIDE.md` (sources and licences, authoring pipeline, YouTube rules, coverage goals), `docs/SECURITY.md` (ASVS, CSP, rate limits, threat model, privacy: PII scrubbing, vault).
### Guardrails (review blockers)
- No placeholders in docs or checklists ("TBD" is a gap with an owner). Backups never public; no secret value in logs, artifacts or docs.
- Changes keep every earlier rule: no answer leaks, free models only via `llm.py`, PII scrubbed, licence gates, RLS in migrations, `ui_copy` keys for any new string, lime/violet/paper roles.

## Tests
- CI: backup job dry-run on push (encrypt + upload to a test path, then delete); weekly restore job green; rate-limit coverage test; CSP e2e on prod config; Lighthouse CI + size-limit budgets; dependency audit; SearXNG integration (container) tests.
- e2e: the whole regression suite (local smoke set 2× with `--retries=0`; full 3 browsers × 6 viewports judged from CI; frozen-DOM axe via `expectNoAxeViolations`) before the final audit.
## Acceptance
1. Backup restores into a scratch project (tested once) and into CI weekly; 30-day retention.
2. CSP strict with zero violations; dependency audit in CI; rate limits on every route.
3. Lighthouse thresholds and installability pass; Today interactive < 2 s on the throttled 4G iPad profile; all four docs written.

## Decisions (lead to check)
- Backups are `age`-encrypted (owner holds the key) in a private HF dataset, run on the Space; needs `HF_BACKUP_TOKEN` + `BACKUP_AGE_RECIPIENT` on the Space (owner, deferred).
- "Lighthouse PWA ≥ 90" is replaced by performance ≥ 90 + an installability check (the PWA category no longer exists).
- Trusted Types ship report-only first; SearXNG runs in the same container and stays off until its tests pass.
