import type { EmailOtpType, SupabaseClient } from "@supabase/supabase-js";
import { safeNext } from "./redirect";

export type CallbackResult =
  | { ok: true; next: string }
  | { ok: false; kind: "expired" | "other-browser" | "denied" | "invalid"; message: string; next: string };

const OTP_TYPES: ReadonlySet<string> = new Set(["magiclink", "email", "signup", "invite", "recovery", "email_change"]);

function params(url: URL): URLSearchParams {
  // Supabase reports errors (and implicit-flow tokens) in the fragment, PKCE codes in the query.
  const merged = new URLSearchParams(url.search);
  const hash = url.hash.startsWith("#") ? url.hash.slice(1) : url.hash;
  for (const [k, v] of new URLSearchParams(hash)) if (!merged.has(k)) merged.set(k, v);
  return merged;
}

function failure(kind: Exclude<CallbackResult, { ok: true }>["kind"], next: string): CallbackResult {
  const message = {
    expired: "This sign-in link has expired or was already used. Request a new one.",
    "other-browser":
      "This link was opened in a different browser or app than the one you requested it from. Request a new link here, then open it in this browser.",
    denied: "Sign-in was cancelled or refused. You can try again.",
    invalid: "This sign-in link is incomplete or not valid. Request a new one.",
  }[kind];
  return { ok: false, kind, message, next };
}

/**
 * Finish a sign-in that came back to /auth/callback, whatever form Supabase used:
 * - `?code=` (PKCE: Google, and magic links requested from this browser)
 * - `?token_hash=&type=` (email templates that link straight to the app; works across browsers)
 * - `#access_token=&refresh_token=` (implicit links, e.g. generated server-side)
 * - `?error=` / `#error=` (expired or refused)
 */
export async function completeSignIn(client: SupabaseClient, url: URL): Promise<CallbackResult> {
  const p = params(url);
  const next = safeNext(p.get("next"));
  const errorCode = p.get("error_code") ?? "";
  const error = p.get("error");
  if (error || errorCode) {
    if (errorCode === "otp_expired" || /expired|invalid/i.test(p.get("error_description") ?? "")) {
      return failure("expired", next);
    }
    return failure(error === "access_denied" ? "denied" : "invalid", next);
  }

  const code = p.get("code");
  if (code) {
    const { error: exErr } = await client.auth.exchangeCodeForSession(code);
    if (!exErr) return { ok: true, next };
    if (/code verifier|code_verifier|flow state/i.test(exErr.message)) return failure("other-browser", next);
    return failure(exErr.code === "otp_expired" || exErr.status === 403 ? "expired" : "invalid", next);
  }

  const tokenHash = p.get("token_hash");
  const type = p.get("type");
  if (tokenHash && type && OTP_TYPES.has(type)) {
    const { error: vErr } = await client.auth.verifyOtp({ token_hash: tokenHash, type: type as EmailOtpType });
    if (!vErr) return { ok: true, next };
    return failure(vErr.status === 403 || vErr.code === "otp_expired" ? "expired" : "invalid", next);
  }

  const accessToken = p.get("access_token");
  const refreshToken = p.get("refresh_token");
  if (accessToken && refreshToken) {
    // setSession validates the access token with Supabase (getUser) before storing it.
    const { error: sErr } = await client.auth.setSession({ access_token: accessToken, refresh_token: refreshToken });
    if (!sErr) return { ok: true, next };
    return failure("expired", next);
  }

  return failure("invalid", next);
}

const inflight = new Map<string, Promise<CallbackResult>>();

/** Same as completeSignIn, but a URL is only ever redeemed once (React StrictMode runs effects twice). */
export function completeSignInOnce(client: SupabaseClient, url: URL): Promise<CallbackResult> {
  const key = url.href;
  let p = inflight.get(key);
  if (!p) {
    p = completeSignIn(client, url);
    inflight.set(key, p);
  }
  return p;
}
