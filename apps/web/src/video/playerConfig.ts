/**
 * Where the YouTube player page lives. The server decides (`/api/public-config` `video_player_origin`, one value that
 * also feeds the app's CSP `frame-src` and the fullscreen Permissions-Policy); the auth layer hands it over once the
 * config is loaded. The default is the production player Space, so a bare test or a preview still has an answer.
 */
export const DEFAULT_PLAYER_ORIGIN = "https://arrowassassin-naadi-player.static.hf.space";

let origin = DEFAULT_PLAYER_ORIGIN;

/** An https origin (or loopback http, for local runs); anything else is refused and the current value kept. */
export function asOrigin(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const u = new URL(value);
    const loopback = u.hostname === "localhost" || u.hostname === "127.0.0.1";
    if (u.protocol !== "https:" && !(loopback && u.protocol === "http:")) return null;
    return u.origin === value ? u.origin : null;
  } catch {
    return null;
  }
}

export function setPlayerOrigin(value: unknown): void {
  origin = asOrigin(value) ?? origin;
}

export function playerOrigin(): string {
  return origin;
}

/** Test hook. */
export function resetPlayerOrigin(): void {
  origin = DEFAULT_PLAYER_ORIGIN;
}
