/**
 * What the service worker may cache (segment 18), as pure functions so the rules are unit-tested:
 *
 * - The app shell, fonts, icons and the pdf.js worker: precached from the build manifest (`injectManifest`).
 * - Figures from the public content bucket (`content-public`): runtime CacheFirst, at most 300 entries.
 * - Nothing else. No `/api/*` response is ever cached by the service worker (data lives in Dexie only), no vault
 *   or other private Storage URL, no YouTube (policy), no signed URL.
 */
export const FIGURE_CACHE = "naadi-figures-v1";
/** App chunks and assets outside the precache, cached on first use (hashed names: never stale). */
export const ASSET_CACHE = "naadi-assets-v1";
export const ASSET_MAX_ENTRIES = 600;
export const FIGURE_MAX_ENTRIES = 300;

const PUBLIC_FIGURES = "/storage/v1/object/public/content-public/";

export function isPublicFigure(url: URL): boolean {
  if (url.protocol !== "https:" && url.protocol !== "http:") return false;
  if (url.search.includes("token=")) return false; // a signed URL is never public content
  return url.pathname.includes(PUBLIC_FIGURES);
}

/** Never served or stored by the service worker, whatever a route says. */
export function isNeverCached(url: URL, selfOrigin: string): boolean {
  if (url.origin === selfOrigin && (url.pathname === "/api" || url.pathname.startsWith("/api/"))) return true;
  if (url.pathname.includes("/storage/v1/object/sign/")) return true;
  if (url.pathname.includes("/storage/v1/object/authenticated/")) return true;
  if (url.pathname.includes("/storage/v1/object/public/") && !isPublicFigure(url)) return true;
  return /(^|\.)youtube(-nocookie)?\.com$|(^|\.)ytimg\.com$|(^|\.)googlevideo\.com$/.test(url.hostname);
}

/** Navigations answered with the precached shell: every same-origin page, never /api or the auth callback. */
export function isShellNavigation(url: URL, selfOrigin: string): boolean {
  if (url.origin !== selfOrigin) return false;
  if (url.pathname.startsWith("/api/") || url.pathname === "/api") return false;
  return !/\.(js|mjs|css|json|webmanifest|png|svg|ico|woff2?|txt|xml|map)$/i.test(url.pathname); // a file is not a page
}

/** The app's own hashed build output (/assets/...), runtime-cached on first use. */
export function isAppAsset(url: URL, selfOrigin: string): boolean {
  return url.origin === selfOrigin && url.pathname.startsWith("/assets/");
}

/** Build-manifest entries to precache: shell, assets, fonts, icons, pdf.js worker; never anything under /api. */
export function precacheable(path: string): boolean {
  const p = path.replace(/^\.?\//, "");
  if (p.startsWith("api/")) return false;
  return /\.(html|js|mjs|css|woff2?|svg|png|ico|webmanifest)$/i.test(p);
}
