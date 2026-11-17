/**
 * What the service worker precaches (segment 18), decided from Vite's build manifest (`dist/.vite/manifest.json`):
 * the app shell and every chunk, so every route opens offline, except syntax-highlighting grammars and themes the
 * app does not use. Code highlighting belongs to the assistant (which needs a connection anyway); offline, a code
 * block falls back to plain text. Budget: at most PRECACHE_BUDGET_BYTES (checked at build time, vite.config.ts).
 */
export const PRECACHE_BUDGET_BYTES = 24 * 1024 * 1024;

/** Shiki themes the app renders with (assistant/Markdown.tsx). */
const KEEP_THEMES = new Set(["github-dark"]);

export interface ViteManifestChunk {
  file: string;
  src?: string;
}

/** True for a manifest source module that is not precached. */
export function skippedSource(src: string): boolean {
  if (/(?:@shikijs\/langs|shiki\/dist\/langs)\//.test(src)) return true;
  const theme = /(?:@shikijs\/themes|shiki\/dist\/themes)\/(?:dist\/)?([a-z0-9-]+)\.m?js/.exec(src);
  return theme !== null && !KEEP_THEMES.has(theme[1] as string);
}

/** The output files to leave out of the precache list. */
export function skippedFiles(manifest: Record<string, ViteManifestChunk>): Set<string> {
  const out = new Set<string>();
  for (const [key, chunk] of Object.entries(manifest)) if (skippedSource(chunk.src ?? key)) out.add(chunk.file);
  return out;
}
