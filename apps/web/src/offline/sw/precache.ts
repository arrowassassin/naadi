/**
 * What the service worker precaches on install (segment 18), decided from Vite's build manifest
 * (`dist/.vite/manifest.json`): the app shell and the static import closures of the core study screens (Today in
 * the shell, the Learn reader with its notes panel, ink and highlights, Practice and the exam view, Cards, Notes,
 * Notebooks), the assistant's renderer with math, and the fonts they use. Budget: PRECACHE_BUDGET_BYTES, enforced
 * at build time (vite.config.ts), because every install downloads it, often on an iPad's mobile data.
 *
 * Everything else (admin, authoring, pathway, generation, the library's PDF viewer, diagram engines, the whiteboard)
 * is cached at run time on first use (caching.ts: CacheFirst, LRU-capped), so a screen opened once online also opens
 * offline afterwards.
 */
export const PRECACHE_BUDGET_BYTES = 5 * 1024 * 1024;

/** Manifest keys whose static closure is precached. A RegExp matches keys that carry a hash or a package path. */
export const PRECACHE_ROOTS: readonly (string | RegExp)[] = [
  "index.html",
  "src/learn/CoursesPage.tsx",
  "src/learn/ReaderPage.tsx",
  /^_PageNotesPanel-/,
  "src/ink/persist/SectionInkLive.tsx",
  "src/highlights/HighlightEngine.tsx",
  "src/practice/BuilderPage.tsx",
  "src/practice/QuestionPage.tsx",
  "src/exam/ExamPage.tsx",
  "src/cards/ReviewPage.tsx",
  "src/notes/NotesPage.tsx",
  "src/notebooks/NotebooksShelf.tsx",
  "src/notebooks/NotebookPage.tsx",
  "src/assistant/AssistantSurface.tsx",
  /\/katex\/dist\/katex\.mjs$/,
  /\/streamdown\/dist\/index\.js$/,
  /\/@streamdown\/math\/dist\/index\.js$/,
];

export interface ViteManifestChunk {
  file: string;
  src?: string;
  imports?: string[];
  css?: string[];
  assets?: string[];
}

/** The output files of the precache roots and their static imports (CSS and the assets the CSS uses included). */
export function coreFiles(
  manifest: Record<string, ViteManifestChunk>,
  roots: readonly (string | RegExp)[] = PRECACHE_ROOTS,
): Set<string> {
  const files = new Set<string>();
  const seen = new Set<string>();
  const walk = (key: string) => {
    const chunk = manifest[key];
    if (!chunk || seen.has(key)) return;
    seen.add(key);
    files.add(chunk.file);
    for (const f of [...(chunk.css ?? []), ...(chunk.assets ?? [])]) files.add(f);
    for (const i of chunk.imports ?? []) walk(i);
  };
  for (const root of roots) {
    if (typeof root === "string") walk(root);
    else for (const key of Object.keys(manifest)) if (root.test(key)) walk(key);
  }
  return files;
}

/** True for a build-manifest entry (a path relative to dist) that the install downloads. */
export function precached(url: string, core: ReadonlySet<string>): boolean {
  const path = url.replace(/^\//, "");
  // Hashed chunks and assets: only the core closure. The rest of the shell (index.html, fonts, icons) always.
  return path.startsWith("assets/") ? core.has(path) : true;
}
