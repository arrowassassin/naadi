/**
 * Image hosts the app's CSP allows (img-src in apps/api/naadi_api/security.py, SEARCH_MEDIA_HOSTS) besides the
 * app's own origin and the Supabase project origin. Chat images from any other host are dropped before they
 * reach the DOM (the CSP would refuse them anyway). mediaHosts.test.ts fails if this drifts from the server.
 */
export const SEARCH_MEDIA_HOSTS = [
  "https://upload.wikimedia.org",
  "https://thumb.wikimedia.org",
  "https://pmc-oa-opendata.s3.amazonaws.com",
] as const;

/** URL prefixes chat images may load from. */
export function imagePrefixes(origin: string, supabaseUrl: string | null): string[] {
  const out = [`${origin}/`, ...SEARCH_MEDIA_HOSTS.map((h) => `${h}/`)];
  if (supabaseUrl) {
    try {
      out.push(`${new URL(supabaseUrl).origin}/storage/`);
    } catch {
      // a malformed config value adds nothing
    }
  }
  return out;
}

/** True for an https URL (or a same-origin path) that starts with one of `prefixes`. */
export function allowedUrl(raw: string, prefixes: readonly string[], origin: string): boolean {
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return false;
  }
  if (url.origin !== origin && url.protocol !== "https:") return false;
  if (url.username || url.password) return false;
  return prefixes.some((p) => url.href.startsWith(p));
}
