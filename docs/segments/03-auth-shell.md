# Segment 03 — M1: Auth (Google + magic link), profiles, admin role, app shell

**Implementer:** `implementer-opus`. Reason: authentication and authorization are security-critical; the shell is the frame every screen inherits.
**Reviewer:** `reviewer-opus` (mandatory).
**Depends on:** 01 (scaffold, contracts, CI, deploy), 02 (profiles table + RLS).
**HANDOFF refs:** §1 (multi-user-ready, single tenant), §4 (design system), §5.1 (targets, keyboard), §8 (Phone/Dock/Nav/System rows), §9. Boards: `design/Nav.dc.html`, `design/Dock.dc.html`, `design/Phone.dc.html`, `design/PhoneToday.dc.html` (tab bar), `design/Main.dc.html` (laptop frame), `design/Tablet.dc.html`, `design/Onboarding.dc.html` (sign-in look).

## Goal
A student can sign in with Google or an email magic link on iPad, iPhone and laptop, lands in a responsive app shell, and the API knows exactly who they are and whether they are an admin. Unauthenticated access to user data is impossible.

## Scope

### Supabase Auth (web)
- `@supabase/supabase-js` 2.119.0, PKCE flow, session persisted (works in Safari and the installed PWA; refresh handled), `onAuthStateChange` wired into TanStack Query (invalidate on sign-in/out).
- Sign-in screen (built from tokens, following the Onboarding board's look): **Continue with Google** (shown only when `GOOGLE_OAUTH_CONFIGURED` is true, exposed through a public config endpoint, never baked into the bundle) and **email magic link** (send → "check your email" state with resend cooldown; invalid/expired link state). Callback route handles both and returns to the page the user came from.
- Sign out (all devices option via `signOut({ scope: 'global' })`).
- Redirect URLs use `APP_BASE_URL`; local dev uses `http://localhost:5173`. (Owner already set Supabase Site URL and redirect URLs.)

### API auth (`naadi_api/auth.py`)
- Verify the Supabase access token on every protected route: JWKS from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` (cached, rotated on `kid` miss; the project uses asymmetric keys — `SUPABASE_JWT_SECRET` HS256 only as a fallback if configured). Check `aud`, `iss`, `exp`, `nbf`; clock skew ≤ 60 s. Prefer the official `supabase` SDK (`auth.get_claims`) if it meets these checks; otherwise a vetted JWT library with a comment why.
- `CurrentUser` dependency: `user_id`, email (never logged), `is_admin`.
- **Admin role:** `is_admin` = email in `ADMIN_EMAILS` (case-insensitive, verified email only). Admin routes enforce it **server-side** (403 for non-admins); the client only hides UI.
- DB access as the user: user-scoped queries run with the user's identity so RLS applies (`set local role authenticated` + `request.jwt.claims` on the pooled connection, or the Supabase client with the user's token); the service role is used only for shared-content writes and never on a user-data path. Test both.
- `GET /api/me` (profile + is_admin), `PATCH /api/me` (display name, timezone, settings) — first sign-in creates the `profiles` row (idempotent).
- `GET /api/public-config` (unauthenticated, cacheable): Supabase URL + anon key, `google_oauth_enabled`, app version. Nothing secret.
- Rate limit auth-adjacent endpoints (magic-link sends are Supabase-side; our `/api/me` etc. get a per-user limiter).

### App shell (web)
- Routes: `/sign-in`, `/auth/callback`, protected layout for everything else; `/system` stays public (design-system reference).
- **Navigation is data-driven:** nav items come from `app_config.ui.nav` (seeded in 05; until then from a server default in the API, not the bundle) and each item has a feature flag. **Only routes that exist are shown** — no dead links, no "coming soon". At the end of this segment the shell shows Today (a real, minimal signed-in home: greeting from the profile, exam date if set, and a link to finish onboarding once 11 exists — until then only the profile) and Settings → Account.
- Laptop/iPad: floating **nav pill** (Nav board: logo, items, search button, avatar → settings) at the top; content max width 1320. Phone (< 640): **bottom tab bar** with the centre assistant button (PhoneToday/Phone boards). The assistant button and assistant bar are rendered **only when the assistant feature flag is on** (segment 14); until then they are absent, not disabled stubs.
- Layout is fluid between breakpoints (phone < 640, tablet 640–1100, laptop > 1100), handles iPad Split View widths, safe-area insets, and the PWA standalone status bar.
- Keyboard: ⌘K/Ctrl+K reserved for search (opens nothing until 07 — do not bind it yet), Tab order, skip-to-content link, visible focus ring (3 px violet, 2 px offset). Reduced motion respected.
- Loading (skeleton), error (retry + request id), offline (banner; cached session still shows the shell) states for the shell and `/api/me`.

## Tests
- API unit: valid token → user; expired, wrong `aud`/`iss`, bad signature, unknown `kid`, missing header → 401 with the error shape; admin route → 403 for non-admin, 200 for admin; ADMIN_EMAILS parsing; tokens/emails never in logs.
- API integration (CI, `supabase/postgres` + a locally minted JWT signed with a test JWKS; and live against the real project with a real test user): `/api/me` creates the profile once; user-scoped query sees only own rows (RLS really applied, not bypassed by service role).
- E2E (all browsers × six viewports, axe): sign-in screen states; magic-link flow end to end using a **real** test inbox-free path — create a test user and generate a magic link server-side with the service role (`auth.admin.generate_link`) in a test-only fixture that runs in CI/sandbox, never shipped in the app; Google button visible iff enabled; protected route redirects when signed out; nav pill vs tab bar by breakpoint; Split View widths 320/507/694 px; sign-out.
- Live Space: sign in with a generated magic link via Playwright WebKit; `/api/me` returns the profile; admin check true for the owner's email (assert only the boolean, never print the email).
- Manual (owner, device checklist): Google sign-in on iPad Safari and the installed PWA.

## Screens × states
Sign-in (default, sending, sent, error, expired link, offline), callback (processing, error), shell + minimal Today (loading, populated, error, offline) and Settings → Account, at 820×1180, 1180×820, 390×844, 412×915, 1440×900, 1920×1080; compare with Nav, Dock, PhoneToday, Main, Onboarding boards.

## Acceptance criteria
1. Magic link and Google (when enabled) sign-in work on all target browsers; sessions survive reload and PWA relaunch.
2. Every protected API route rejects missing/invalid tokens; admin routes are server-gated; RLS is the enforcement path for user data.
3. The shell matches the boards at every breakpoint, has no dead navigation, and passes axe with zero violations and zero console errors.
4. No secret, token or email appears in logs, responses (beyond the user's own `/api/me`) or the bundle.
