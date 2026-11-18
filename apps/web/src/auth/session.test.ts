/**
 * The deferred client (segment 22): a start with a stored session needs neither the network nor supabase-js,
 * and once the client loads it owns refresh, auth events and sign-out exactly as before.
 */
import type { Session } from "@supabase/supabase-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./publicConfig", () => ({
  loadPublicConfig: vi.fn(async () => ({
    fromCache: false,
    config: {
      auth_configured: true,
      supabase_url: "https://project.supabase.test",
      supabase_anon_key: "public-anon-key",
      video_player_origin: null,
    },
  })),
}));

import {
  AUTH_STORAGE_KEY,
  accessToken,
  EXPIRY_MARGIN_S,
  getAuthState,
  isFresh,
  onAuthChange,
  refreshAccessToken,
  resetAuthForTests,
  signOut,
  startAuth,
  supabaseClient,
} from "./session";

type Listener = (event: string, session: Session | null) => void;

function session(userId: string, token: string, expiresInS: number): Session {
  return {
    access_token: token,
    refresh_token: `r-${token}`,
    token_type: "bearer",
    expires_in: expiresInS,
    expires_at: Math.floor(Date.now() / 1000) + expiresInS,
    user: { id: userId } as Session["user"],
  } as Session;
}

/** A stand-in for supabase-js: records calls; `emit` plays an auth event; `gate` holds the import. */
function fakeSupabase(opts: { getSession?: () => Promise<unknown>; failImport?: boolean } = {}) {
  let listener: Listener | null = null;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((r) => {
    release = r;
  });
  const auth = {
    getSession: vi.fn(opts.getSession ?? (async () => ({ data: { session: null }, error: null }))),
    refreshSession: vi.fn(async () => ({ data: { session: session("u1", "refreshed", 3600) }, error: null })),
    signOut: vi.fn(async () => ({ error: null })),
    onAuthStateChange: vi.fn((l: Listener) => {
      listener = l;
      return { data: { subscription: { unsubscribe() {} } } };
    }),
  };
  const createClient = vi.fn(() => ({ auth }));
  const load = vi.fn(async () => {
    await gate;
    if (opts.failImport) throw new TypeError("Failed to fetch dynamically imported module");
    return { createClient } as never;
  });
  return { auth, createClient, load, release, emit: (e: string, s: Session | null) => listener?.(e, s) };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function setOnline(v: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => v });
}

beforeEach(() => {
  localStorage.clear();
  setOnline(true);
});
afterEach(() => resetAuthForTests());

describe("start with a stored session", () => {
  it("a fresh token signs in without waiting for supabase-js, which then loads in the background", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: session("u1", "stored", 3600) }, error: null }),
    });
    resetAuthForTests(sb.load);
    const s = await startAuth();
    expect(s.status).toBe("signed-in");
    expect(s.status === "signed-in" && s.session.access_token).toBe("stored");
    // The token is usable at once; the client has not been created yet.
    expect(await accessToken()).toBe("stored");
    expect(sb.createClient).not.toHaveBeenCalled();
    sb.release();
    await flush();
    expect(sb.createClient).toHaveBeenCalledTimes(1);
    expect(sb.auth.onAuthStateChange).toHaveBeenCalledTimes(1);
  });

  it("an expired token waits for the client to refresh it", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "old", -60)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: session("u1", "new", 3600) }, error: null }),
    });
    resetAuthForTests(sb.load);
    const started = startAuth();
    await flush();
    expect(getAuthState().status).toBe("loading");
    sb.release();
    const s = await started;
    expect(s.status === "signed-in" && s.session.access_token).toBe("new");
  });

  it("a token inside the expiry margin is not sent as is", async () => {
    const near = session("u1", "near", EXPIRY_MARGIN_S - 5);
    expect(isFresh(near)).toBe(false);
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(near));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: session("u1", "new", 3600) }, error: null }),
    });
    resetAuthForTests(sb.load);
    sb.release();
    await startAuth();
    expect(await accessToken()).toBe("new");
  });

  it("expired and offline: the shell keeps the stored session, marked offline", async () => {
    setOnline(false);
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "old", -60)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: null }, error: { name: "AuthRetryableFetchError", status: 0 } }),
    });
    resetAuthForTests(sb.load);
    sb.release();
    const s = await startAuth();
    expect(s.status === "signed-in" && s.offline).toBe(true);
    expect(await accessToken()).toBe("old");
  });

  it("expired and the client cannot be fetched (offline start): signed in from storage, marked offline", async () => {
    setOnline(false);
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "old", -60)));
    const sb = fakeSupabase({ failImport: true });
    resetAuthForTests(sb.load);
    sb.release();
    const s = await startAuth();
    expect(s.status === "signed-in" && s.offline).toBe(true);
  });

  it("expired online and the refresh token was revoked: signed out", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "old", -60)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: null }, error: { name: "AuthApiError", status: 400 } }),
    });
    resetAuthForTests(sb.load);
    sb.release();
    expect((await startAuth()).status).toBe("signed-out");
  });

  it("the background check signs out when the client no longer holds the session", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({ getSession: async () => ({ data: { session: null }, error: null }) });
    resetAuthForTests(sb.load);
    const events: string[] = [];
    onAuthChange((e) => events.push(e));
    await startAuth();
    sb.release();
    await flush();
    expect(getAuthState().status).toBe("signed-out");
    expect(events).toContain("SIGNED_OUT");
  });
});

describe("auth events", () => {
  it("before the client loads, sign-out in another tab (the storage key removed) signs this tab out", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase();
    resetAuthForTests(sb.load);
    await startAuth();
    const events: Array<[string, string | null, string | null]> = [];
    onAuthChange((e, prev, next) => events.push([e, prev, next]));
    localStorage.removeItem(AUTH_STORAGE_KEY);
    window.dispatchEvent(new StorageEvent("storage", { key: AUTH_STORAGE_KEY, newValue: null }));
    expect(getAuthState().status).toBe("signed-out");
    expect(events).toEqual([["SIGNED_OUT", "u1", null]]);
  });

  it("before the client loads, a refresh in another tab updates the token without a user change", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase();
    resetAuthForTests(sb.load);
    await startAuth();
    const events: string[] = [];
    onAuthChange((e) => events.push(e));
    const next = JSON.stringify(session("u1", "other-tab", 3600));
    window.dispatchEvent(new StorageEvent("storage", { key: AUTH_STORAGE_KEY, newValue: next }));
    expect(await accessToken()).toBe("other-tab");
    expect(events).toEqual(["TOKEN_REFRESHED"]);
  });

  it("once loaded, the client's events drive the state (sign-out elsewhere, sign-in from the callback)", async () => {
    const sb = fakeSupabase();
    resetAuthForTests(sb.load);
    sb.release();
    expect((await startAuth()).status).toBe("signed-out");
    // The callback page asks for the client and redeems the code with it; supabase-js then reports SIGNED_IN.
    const client = await supabaseClient();
    expect(client).toBeTruthy();
    sb.emit("SIGNED_IN", session("u2", "fresh", 3600));
    expect(getAuthState().status).toBe("signed-in");
    sb.emit("SIGNED_OUT", null);
    expect(getAuthState().status).toBe("signed-out");
  });
});

describe("refresh and sign-out", () => {
  it("refreshAccessToken loads the client and refreshes", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: session("u1", "stored", 3600) }, error: null }),
    });
    resetAuthForTests(sb.load);
    await startAuth();
    sb.release();
    expect(await refreshAccessToken()).toBe("refreshed");
    expect(sb.auth.refreshSession).toHaveBeenCalledTimes(1);
  });

  it("local sign-out clears this device even when the client cannot be fetched", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({ failImport: true });
    resetAuthForTests(sb.load);
    await startAuth();
    sb.release();
    await flush();
    expect(await signOut("local")).toEqual({ ok: true });
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(getAuthState().status).toBe("signed-out");
  });

  it("global sign-out without the client says so and keeps the session", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({ failImport: true });
    resetAuthForTests(sb.load);
    await startAuth();
    sb.release();
    await flush();
    const r = await signOut("global");
    expect(r.ok).toBe(false);
    expect(getAuthState().status).toBe("signed-in");
  });

  it("sign-out goes through supabase-js with the requested scope", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session("u1", "stored", 3600)));
    const sb = fakeSupabase({
      getSession: async () => ({ data: { session: session("u1", "stored", 3600) }, error: null }),
    });
    resetAuthForTests(sb.load);
    sb.release();
    await startAuth();
    await signOut("global");
    expect(sb.auth.signOut).toHaveBeenCalledWith({ scope: "global" });
    expect(getAuthState().status).toBe("signed-out");
  });
});
