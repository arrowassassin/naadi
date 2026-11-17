import type { PublicConfig } from "@naadi/shared";
import type { AuthChangeEvent, Session, SupabaseClient } from "@supabase/supabase-js";
import { useEffect, useSyncExternalStore } from "react";
import { setPlayerOrigin } from "../video/playerConfig";
import { ApiError } from "./apiError";
import { loadPublicConfig } from "./publicConfig";
import { clearUserCaches } from "./userCache";

export const AUTH_STORAGE_KEY = "naadi.auth";

/**
 * Auth state for the app. supabase-js (about 55 KB gzip) is not part of the first load (segment 22 budget): a
 * start with a stored, unexpired session is signed in straight from that session, and the client loads right
 * after in the background. It then owns the session as before: token refresh, auth events (including sign-in and
 * sign-out in other tabs) and Storage. The client is awaited first only when the stored token has expired (it must
 * be refreshed) or a caller needs it (`supabaseClient()`, `useSupabaseClient()`).
 */
export type AuthState =
  | { status: "loading" }
  | { status: "unavailable"; error: ApiError }
  | { status: "signed-out"; config: PublicConfig }
  | {
      status: "signed-in";
      config: PublicConfig;
      session: Session;
      /** The stored session could not be refreshed because the network is down. */
      offline: boolean;
    };

type Listener = () => void;
type ChangeHook = (event: AuthChangeEvent | "INITIAL", previousUserId: string | null, userId: string | null) => void;

/** A stored token this close to expiry is refreshed by the client before use, never sent as is. */
export const EXPIRY_MARGIN_S = 90;

let state: AuthState = { status: "loading" };
let starting: Promise<AuthState> | null = null;
const listeners = new Set<Listener>();
const changeHooks = new Set<ChangeHook>();

let client: SupabaseClient | null = null;
let clientLoad: Promise<SupabaseClient> | null = null;
const clientListeners = new Set<Listener>();

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

function parseSession(raw: string | null): Session | null {
  try {
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s?.access_token && s.user?.id ? s : null;
  } catch {
    return null;
  }
}

function readStoredSession(): Session | null {
  try {
    return parseSession(localStorage.getItem(AUTH_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** The stored access token is good for at least EXPIRY_MARGIN_S more seconds (by this device's clock). */
export function isFresh(session: Session, nowMs = Date.now()): boolean {
  return typeof session.expires_at === "number" && session.expires_at - EXPIRY_MARGIN_S > nowMs / 1000;
}

function configOf(s: AuthState): PublicConfig | null {
  return s.status === "signed-in" || s.status === "signed-out" ? s.config : null;
}

function assertConfigured(config: PublicConfig): void {
  if (!config.supabase_url || !config.supabase_anon_key) {
    throw new ApiError("Sign-in is not configured on this server.", null, "auth_unconfigured", null);
  }
}

/** Test hook: the module that provides `createClient` (the real one is imported on first use). */
export type ClientModule = Pick<typeof import("@supabase/supabase-js"), "createClient">;
let importClientModule = (): Promise<ClientModule> => import("@supabase/supabase-js");

/** Load supabase-js once, create the client and wire its auth events. A failed load (offline) can be retried. */
function loadClient(config: PublicConfig): Promise<SupabaseClient> {
  if (client) return Promise.resolve(client);
  if (clientLoad) return clientLoad;
  const load = importClientModule().then(({ createClient }) => {
    assertConfigured(config);
    const c = createClient(config.supabase_url as string, config.supabase_anon_key as string, {
      auth: {
        flowType: "pkce",
        persistSession: true,
        autoRefreshToken: true,
        // The /auth/callback route exchanges the code itself, so a code is never redeemed twice.
        detectSessionInUrl: false,
        storageKey: AUTH_STORAGE_KEY,
      },
    });
    c.auth.onAuthStateChange((event, session) => {
      // Never await Supabase calls inside this callback (supabase-js holds a lock while it runs).
      if (event === "INITIAL_SESSION") return;
      if (session) {
        setState({ status: "signed-in", config, session, offline: false }, event);
      } else if (event === "SIGNED_OUT") {
        setState({ status: "signed-out", config }, event);
      }
    });
    client = c;
    for (const l of clientListeners) l();
    return c;
  });
  clientLoad = load;
  load.catch(() => {
    if (clientLoad === load) clientLoad = null;
  });
  return load;
}

/**
 * Bring the state in line with what the client holds, once it has loaded: the same decisions the start used to make
 * with the client in hand (a refreshed session, an offline expired one, or none).
 */
async function reconcile(c: SupabaseClient, config: PublicConfig): Promise<void> {
  const { data, error } = await c.auth.getSession();
  const cur = getAuthState();
  if (data.session) {
    const same = cur.status === "signed-in" && !cur.offline && cur.session.access_token === data.session.access_token;
    if (!same) setState({ status: "signed-in", config, session: data.session, offline: false }, "TOKEN_REFRESHED");
    return;
  }
  if (cur.status !== "signed-in") return;
  if (error && !navigator.onLine) {
    if (!cur.offline) setState({ ...cur, offline: true }, "TOKEN_REFRESHED");
  } else {
    setState({ status: "signed-out", config }, "SIGNED_OUT");
  }
}

/** Before the client has loaded, follow sign-in and sign-out in other tabs from the shared storage key. */
function onStorage(e: StorageEvent): void {
  if (client || e.key !== AUTH_STORAGE_KEY) return;
  const config = configOf(getAuthState());
  if (!config) return;
  const next = parseSession(e.newValue);
  if (!next) {
    if (getAuthState().status === "signed-in") setState({ status: "signed-out", config }, "SIGNED_OUT");
  } else {
    const same = userIdOf(getAuthState()) === next.user.id;
    setState({ status: "signed-in", config, session: next, offline: false }, same ? "TOKEN_REFRESHED" : "SIGNED_IN");
  }
}

function loadInBackground(config: PublicConfig): void {
  void loadClient(config)
    .then((c) => reconcile(c, config))
    .catch(() => {
      // Offline or the chunk is unavailable: the stored session stays in use; the next call that needs the client
      // (a refresh, sign-out, Storage) tries again, and so does going back online.
    });
}

async function boot(): Promise<AuthState> {
  let config: PublicConfig;
  try {
    ({ config } = await loadPublicConfig());
    setPlayerOrigin(config.video_player_origin);
    assertConfigured(config);
  } catch (err) {
    const error = err instanceof ApiError ? err : new ApiError(String(err), null, null, null);
    const s: AuthState = { status: "unavailable", error };
    setState(s, "INITIAL");
    return s;
  }

  window.addEventListener("storage", onStorage);
  window.addEventListener("online", () => {
    const s = getAuthState();
    if (s.status === "signed-in" && s.offline) loadInBackground(config);
  });

  const stored = readStoredSession();
  let initial: AuthState;
  if (!stored || isFresh(stored)) {
    // Signed out, or signed in with a token that is still good: no network and no supabase-js needed to decide.
    initial = stored
      ? { status: "signed-in", config, session: stored, offline: false }
      : { status: "signed-out", config };
    setState(initial, "INITIAL");
    loadInBackground(config);
    return initial;
  }

  // The stored token has expired: the client refreshes it (or reports that it cannot).
  let c: SupabaseClient;
  try {
    c = await loadClient(config);
  } catch {
    // The client could not be fetched (a network failure, like being offline): keep showing the shell from the stored
    // session, marked offline; going back online retries the refresh.
    initial = { status: "signed-in", config, session: stored, offline: true };
    setState(initial, "INITIAL");
    return initial;
  }
  const { data, error } = await c.auth.getSession();
  if (data.session) {
    initial = { status: "signed-in", config, session: data.session, offline: false };
  } else {
    // An expired token that cannot be refreshed offline: keep showing the shell from the stored session.
    initial =
      error && !navigator.onLine
        ? { status: "signed-in", config, session: stored, offline: true }
        : { status: "signed-out", config };
  }
  setState(initial, "INITIAL");
  return initial;
}

/** Start auth once (idempotent) and resolve when the first state is known. */
export function startAuth(): Promise<AuthState> {
  if (!starting) starting = boot();
  return starting;
}

/** The Supabase client (loaded on first use). Rejects while sign-in is unavailable on this server. */
export async function supabaseClient(): Promise<SupabaseClient> {
  const s = await startAuth().then(() => getAuthState());
  const config = configOf(s);
  if (!config) throw s.status === "unavailable" ? s.error : new ApiError("Sign-in is not ready.", null, null, null);
  return loadClient(config);
}

/** The client for a component (null until it has loaded; asking for it starts the load). */
export function useSupabaseClient(): SupabaseClient | null {
  const c = useSyncExternalStore(
    (l) => {
      clientListeners.add(l);
      return () => clientListeners.delete(l);
    },
    () => client,
    () => client,
  );
  const auth = useAuth();
  useEffect(() => {
    const config = configOf(auth);
    if (!c && config) void loadClient(config).catch(() => undefined);
  }, [c, auth]);
  return c;
}

/** A current access token, refreshed by supabase-js when it is close to expiry. */
export async function accessToken(): Promise<string | null> {
  const s = await startAuth().then(() => getAuthState());
  if (s.status !== "signed-in") return null;
  if (!client && isFresh(s.session)) return s.session.access_token;
  let c: SupabaseClient;
  try {
    c = await loadClient(s.config);
  } catch {
    return s.offline ? s.session.access_token : null;
  }
  const { data } = await c.auth.getSession();
  return data.session?.access_token ?? (s.offline ? s.session.access_token : null);
}

/** Force a refresh after the API said the token expired; false when the session is gone. */
export async function refreshAccessToken(): Promise<string | null> {
  const s = getAuthState();
  if (s.status !== "signed-in") return null;
  let c: SupabaseClient;
  try {
    c = await loadClient(s.config);
  } catch {
    return null;
  }
  const { data, error } = await c.auth.refreshSession();
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
    let c: SupabaseClient | null = null;
    try {
      c = await loadClient(s.config);
    } catch {
      if (scope === "global") {
        return { ok: false, message: "Could not reach the server to sign out other devices. Try again online." };
      }
    }
    if (c) {
      const { error } = await c.auth.signOut({ scope });
      if (error) {
        if (scope === "global") {
          return { ok: false, message: "Could not reach the server to sign out other devices. Try again online." };
        }
        await c.auth.signOut({ scope: "local" });
        result = { ok: true };
      }
    }
  }
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // ignore
  }
  clearUserCaches();
  if (getAuthState().status !== "signed-out") {
    setState({ status: "signed-out", config: s.config }, "SIGNED_OUT");
  }
  return result;
}

/** Test hook: start again from nothing, with a stand-in for supabase-js. */
export function resetAuthForTests(load?: () => Promise<ClientModule>): void {
  state = { status: "loading" };
  starting = null;
  client = null;
  clientLoad = null;
  listeners.clear();
  changeHooks.clear();
  clientListeners.clear();
  window.removeEventListener("storage", onStorage);
  if (load) importClientModule = load;
}
