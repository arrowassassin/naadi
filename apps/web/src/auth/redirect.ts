/**
 * Where to go after sign-in. Only same-origin, absolute paths are allowed, so a crafted
 * `?redirect=https://evil.example` or `//evil.example` can never send the user off-site.
 */
const AUTH_PATHS = ["/sign-in", "/auth/callback"];

export function safeNext(raw: unknown, fallback = "/"): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 2048) return fallback;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return fallback;
  // Control characters and backslashes are never part of an in-app path.
  for (let i = 0; i < raw.length; i++) {
    const c = raw.charCodeAt(i);
    if (c < 0x20 || c === 0x7f || raw[i] === "\\") return fallback;
  }
  let url: URL;
  try {
    url = new URL(raw, "https://naadi.invalid");
  } catch {
    return fallback;
  }
  if (url.origin !== "https://naadi.invalid") return fallback;
  if (AUTH_PATHS.some((p) => url.pathname === p || url.pathname.startsWith(`${p}/`))) return fallback;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** The origin Supabase should send the user back to: the canonical APP_BASE_URL, except in local dev. */
export function redirectOrigin(appBaseUrl: string | null | undefined, here: Location = window.location): string {
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(here.hostname);
  if (!appBaseUrl || loopback) return here.origin;
  try {
    return new URL(appBaseUrl).origin;
  } catch {
    return here.origin;
  }
}

export function callbackUrl(origin: string, next: string): string {
  const u = new URL("/auth/callback", origin);
  if (next !== "/") u.searchParams.set("next", next);
  return u.toString();
}
