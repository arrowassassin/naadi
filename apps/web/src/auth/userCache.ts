/**
 * Last good `/api/me` and `/api/nav` per user, so an offline launch still shows the shell. The Supabase
 * session (which already holds the user's email) lives in the same storage; both are cleared on sign-out.
 */
const PREFIX = "naadi.user-cache.v1:";

export function readUserCache<T>(kind: "me" | "nav", userId: string): T | undefined {
  try {
    const raw = localStorage.getItem(`${PREFIX}${kind}:${userId}`);
    return raw ? (JSON.parse(raw) as T) : undefined;
  } catch {
    return undefined;
  }
}

export function writeUserCache(kind: "me" | "nav", userId: string, value: unknown): void {
  try {
    localStorage.setItem(`${PREFIX}${kind}:${userId}`, JSON.stringify(value));
  } catch {
    // quota / private mode: only the offline fallback is lost
  }
}

export function clearUserCaches(): void {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k?.startsWith(PREFIX)) localStorage.removeItem(k);
    }
  } catch {
    // ignore
  }
}
