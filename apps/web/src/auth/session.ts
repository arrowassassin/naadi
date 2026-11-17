import type { PublicConfig } from "@naadi/shared";
import { type AuthChangeEvent, createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { useSyncExternalStore } from "react";
import { setPlayerOrigin } from "../video/playerConfig";
import { ApiError } from "./apiError";
import { loadPublicConfig } from "./publicConfig";
import { clearUserCaches } from "./userCache";

export const AUTH_STORAGE_KEY = "naadi.auth";

export type AuthState =
  | { status: "loading" }
  | { status: "unavailable"; error: ApiError }
  | { status: "signed-out"; config: PublicConfig; client: SupabaseClient }
  | {
      status: "signed-in";
      config: PublicConfig;
      client: SupabaseClient;
      session: Session;
      /** The stored session could not be refreshed because the network is down. */
      offline: boolean;
    };

type Listener = () => void;
type ChangeHook = (event: AuthChangeEvent | "INITIAL", previousUserId: string | null, userId: string | null) => void;

let state: AuthState = { status: "loading" };
let starting: Promise<AuthState> | null = null;
const listeners = new Set<Listener>();
const changeHooks = new Set<ChangeHook>();

function userIdOf(s: AuthState): string | null {
  return s.status === "signed-in" ? s.session.user.id : null;
}

function setState(next: AuthState, event: AuthChangeEvent | "INITIAL"): void {
  const prev = userIdOf(state);
  state = next;
  for (const l of listeners) l();
  const now = userIdOf(next);
  for (const h of changeHooks) h(event, prev, now);
}

export function getAuthState(): AuthState {
  return state;
}

export function subscribeAuth(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Called after every auth change with the previous and current user id (used to reset caches). */
export function onAuthChange(hook: ChangeHook): () => void {
  changeHooks.add(hook);
  return () => changeHooks.delete(hook);
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
}

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s?.access_token && s.user?.id ? s : null;
  } catch {
    return null;
  }
}

function makeClient(config: PublicConfig): SupabaseClient {
  if (!config.supabase_url || !config.supabase_anon_key) {
    throw new ApiError("Sign-in is not configured on this server.", null, "auth_unconfigured", null);
  }
  return createClient(config.supabase_url, config.supabase_anon_key, {
    auth: {
      flowType: "pkce",
      persistSession: true,
      autoRefreshToken: true,
      // The /auth/callback route exchanges the code itself, so a code is never redeemed twice.
      detectSessionInUrl: false,
      storageKey: AUTH_STORAGE_KEY,
    },
  });
}

async function boot(): Promise<AuthState> {
  let config: PublicConfig;
  let client: SupabaseClient;
  try {
    ({ config } = await loadPublicConfig());
    setPlayerOrigin(config.video_player_origin);
    client = makeClient(config);
  } catch (err) {
    const error = err instanceof ApiError ? err : new ApiError(String(err), null, null, null);
    const s: AuthState = { status: "unavailable", error };
    setState(s, "INITIAL");
    return s;
  }

  const { data, error } = await client.auth.getSession();
  let initial: AuthState;
  if (data.session) {
    initial = { status: "signed-in", config, client, session: data.session, offline: false };
  } else {
    // An expired token that cannot be refreshed offline: keep showing the shell from the stored session.
    const stored = error && !navigator.onLine ? readStoredSession() : null;
    initial = stored
      ? { status: "signed-in", config, client, session: stored, offline: true }
      : { status: "signed-out", config, client };
  }
  setState(initial, "INITIAL");

  client.auth.onAuthStateChange((event, session) => {
    // Never await Supabase calls inside this callback (supabase-js holds a lock while it runs).
    if (event === "INITIAL_SESSION") return;
    if (session) {
      setState({ status: "signed-in", config, client, session, offline: false }, event);
    } else if (event === "SIGNED_OUT") {
      setState({ status: "signed-out", config, client }, event);
    }
  });
  window.addEventListener("online", () => {
    const s = getAuthState();
    if (s.status === "signed-in" && s.offline) void client.auth.getSession();
  });
  return initial;
}

/** Start auth once (idempotent) and resolve when the first state is known. */
export function startAuth(): Promise<AuthState> {
  if (!starting) starting = boot();
  return starting;
}

/** A current access token, refreshed by supabase-js when it is close to expiry. */
export async function accessToken(): Promise<string | null> {
  const s = await startAuth().then(() => getAuthState());
  if (s.status !== "signed-in") return null;
  const { data } = await s.client.auth.getSession();
  return data.session?.access_token ?? (s.offline ? s.session.access_token : null);
}

/** Force a refresh after the API said the token expired; false when the session is gone. */
export async function refreshAccessToken(): Promise<string | null> {
  const s = getAuthState();
  if (s.status !== "signed-in") return null;
  const { data, error } = await s.client.auth.refreshSession();
  if (error || !data.session) return null;
  return data.session.access_token;
}

export type SignOutScope = "local" | "global";

/**
 * Sign out on this device, or everywhere (`global` revokes every refresh token of the user). Local data
 * is always cleared, even when the network is down.
 */
export async function signOut(scope: SignOutScope): Promise<{ ok: boolean; message?: string }> {
  const s = getAuthState();
  if (s.status === "loading" || s.status === "unavailable") return { ok: true };
  let result: { ok: boolean; message?: string } = { ok: true };
  if (s.status === "signed-in") {
    const { error } = await s.client.auth.signOut({ scope });
    if (error) {
      if (scope === "global") {
        return { ok: false, message: "Could not reach the server to sign out other devices. Try again online." };
      }
      await s.client.auth.signOut({ scope: "local" });
      result = { ok: true };
    }
  }
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // ignore
  }
  clearUserCaches();
  if (getAuthState().status !== "signed-out") {
    setState({ status: "signed-out", config: s.config, client: s.client }, "SIGNED_OUT");
  }
  return result;
}
