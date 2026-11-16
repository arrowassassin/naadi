import type { SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import { daysUntil, greeting, localDate } from "../routes/time";
import { completeSignIn, completeSignInOnce } from "./callback";
import { callbackUrl, redirectOrigin, safeNext } from "./redirect";

describe("safeNext", () => {
  it.each([
    ["/settings/account", "/settings/account"],
    ["/settings/account?tab=1#x", "/settings/account?tab=1#x"],
    ["/", "/"],
  ])("keeps in-app path %s", (raw, want) => {
    expect(safeNext(raw)).toBe(want);
  });

  it.each([
    "https://evil.example/",
    "//evil.example/x",
    "/\\evil.example",
    "javascript:alert(1)",
    "settings",
    "",
    "/sign-in?redirect=/x",
    "/auth/callback?code=1",
    "/a\nb",
    "/a\\b",
    "x".repeat(3000),
  ])("rejects %s", (raw) => {
    expect(safeNext(raw)).toBe("/");
  });

  it("rejects non-strings", () => {
    expect(safeNext(undefined)).toBe("/");
    expect(safeNext(42)).toBe("/");
  });
});

describe("redirect origin", () => {
  const at = (href: string) => new URL(href) as unknown as Location;
  it("uses APP_BASE_URL in production", () => {
    expect(redirectOrigin("https://naadi.example.org/app", at("https://naadi.example.org/sign-in"))).toBe(
      "https://naadi.example.org",
    );
  });
  it("uses the current origin on localhost and when unset", () => {
    expect(redirectOrigin("https://naadi.example.org", at("http://localhost:5173/sign-in"))).toBe(
      "http://localhost:5173",
    );
    expect(redirectOrigin(null, at("https://x.hf.space/sign-in"))).toBe("https://x.hf.space");
  });
  it("builds the callback with next only when needed", () => {
    expect(callbackUrl("https://a.org", "/")).toBe("https://a.org/auth/callback");
    expect(callbackUrl("https://a.org", "/settings/account")).toBe(
      "https://a.org/auth/callback?next=%2Fsettings%2Faccount",
    );
  });
});

function fakeClient(over: Partial<Record<string, unknown>> = {}) {
  const auth = {
    exchangeCodeForSession: vi.fn(async () => ({ error: null })),
    verifyOtp: vi.fn(async () => ({ error: null })),
    setSession: vi.fn(async () => ({ error: null })),
    ...over,
  };
  return { client: { auth } as unknown as SupabaseClient, auth };
}

describe("completeSignIn", () => {
  it("exchanges a PKCE code and returns next", async () => {
    const { client, auth } = fakeClient();
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback?code=abc&next=%2Fsettings%2Faccount"));
    expect(r).toEqual({ ok: true, next: "/settings/account" });
    expect(auth.exchangeCodeForSession).toHaveBeenCalledWith("abc");
  });

  it("reports a missing code verifier as another browser", async () => {
    const { client } = fakeClient({
      exchangeCodeForSession: vi.fn(async () => ({
        error: { message: "invalid request: both auth code and code verifier should be non-empty", status: 400 },
      })),
    });
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback?code=abc"));
    expect(r.ok).toBe(false);
    expect(!r.ok && r.kind).toBe("other-browser");
  });

  it("verifies a token hash", async () => {
    const { client, auth } = fakeClient();
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback?token_hash=h&type=magiclink"));
    expect(r.ok).toBe(true);
    expect(auth.verifyOtp).toHaveBeenCalledWith({ token_hash: "h", type: "magiclink" });
  });

  it("refuses an unknown otp type", async () => {
    const { client, auth } = fakeClient();
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback?token_hash=h&type=sms"));
    expect(r.ok).toBe(false);
    expect(auth.verifyOtp).not.toHaveBeenCalled();
  });

  it("maps a 403 verify to expired", async () => {
    const { client } = fakeClient({
      verifyOtp: vi.fn(async () => ({ error: { status: 403, code: "otp_expired", message: "expired" } })),
    });
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback?token_hash=h&type=email"));
    expect(!r.ok && r.kind).toBe("expired");
  });

  it("reads errors and implicit tokens from the fragment", async () => {
    const { client, auth } = fakeClient();
    const expired = await completeSignIn(
      client,
      new URL("https://a.org/auth/callback#error=access_denied&error_code=otp_expired"),
    );
    expect(!expired.ok && expired.kind).toBe("expired");
    const denied = await completeSignIn(client, new URL("https://a.org/auth/callback?error=access_denied"));
    expect(!denied.ok && denied.kind).toBe("denied");
    const ok = await completeSignIn(client, new URL("https://a.org/auth/callback#access_token=a&refresh_token=r"));
    expect(ok.ok).toBe(true);
    expect(auth.setSession).toHaveBeenCalledWith({ access_token: "a", refresh_token: "r" });
  });

  it("never follows an off-site next", async () => {
    const { client } = fakeClient();
    const r = await completeSignIn(
      client,
      new URL("https://a.org/auth/callback?code=c&next=https%3A%2F%2Fevil.example"),
    );
    expect(r).toEqual({ ok: true, next: "/" });
  });

  it("redeems a URL only once", async () => {
    const { client, auth } = fakeClient();
    const url = new URL("https://a.org/auth/callback?code=once");
    await Promise.all([completeSignInOnce(client, url), completeSignInOnce(client, url)]);
    expect(auth.exchangeCodeForSession).toHaveBeenCalledTimes(1);
  });

  it("an empty callback is invalid", async () => {
    const { client } = fakeClient();
    const r = await completeSignIn(client, new URL("https://a.org/auth/callback"));
    expect(!r.ok && r.kind).toBe("invalid");
  });
});

describe("time helpers", () => {
  const now = new Date("2026-11-16T20:30:00Z"); // 02:00 on 17 Nov in Kolkata
  it("uses the profile time zone, not the device", () => {
    expect(localDate(now, "Asia/Kolkata")).toBe("2026-11-17");
    expect(localDate(now, "America/New_York")).toBe("2026-11-16");
    expect(daysUntil("2026-11-20", now, "Asia/Kolkata")).toBe(3);
    expect(daysUntil("2026-11-20", now, "America/New_York")).toBe(4);
    expect(daysUntil("2026-11-10", now, "UTC")).toBe(-6);
    expect(greeting(now, "Asia/Kolkata")).toBe("Good evening");
    expect(greeting(new Date("2026-11-16T04:00:00Z"), "Asia/Kolkata")).toBe("Good morning");
  });
});
