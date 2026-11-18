import { getPublicConfig, type PublicConfig } from "@naadi/shared";
import { ApiError, unwrap } from "./apiError";

const CACHE_KEY = "naadi.public-config.v1";
const TIMEOUT_MS = 8000;

function readCache(): PublicConfig | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as PublicConfig) : null;
  } catch {
    return null;
  }
}

function writeCache(cfg: PublicConfig): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cfg));
  } catch {
    // Private mode / quota: the app still works online without the cache.
  }
}

export interface LoadedConfig {
  config: PublicConfig;
  /** true when the network failed and the last good copy was used (offline launch). */
  fromCache: boolean;
}

/**
 * GET /api/public-config. The Supabase URL and anon key come from the server at runtime, never from the
 * bundle. The last good copy is kept so an offline relaunch (PWA) can still restore the cached session.
 */
export async function loadPublicConfig(): Promise<LoadedConfig> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const config = await unwrap(getPublicConfig({ signal: ctrl.signal }));
    writeCache(config);
    return { config, fromCache: false };
  } catch (err) {
    const cached = readCache();
    if (cached) return { config: cached, fromCache: true };
    if (err instanceof ApiError) throw err;
    throw new ApiError("The server could not be reached. Check your connection and try again.", null, null, null);
  } finally {
    clearTimeout(timer);
  }
}
