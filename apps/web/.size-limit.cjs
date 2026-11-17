// Performance budgets (segment 22, HANDOFF §12): read from a build made with `vite build --manifest`
// (`pnpm --filter @naadi/web size`), so the entries follow the real chunk graph instead of hand-kept globs.
//
// - initial JS  <= 250 KB gzip: every script index.html loads or modulepreloads (what Today needs to start);
// - initial CSS <= 40 KB gzip: the stylesheets index.html links;
// - each route page chunk <= 150 KB gzip: every lazy chunk built from a page under src/ (lazyPage, lazy());
//   its shared imports are measured once, in the chunks they land in.
// Libraries the brief requires to be lazy and that are large by nature (Mermaid, Shiki, KaTeX, pdf.js,
// Excalidraw, TipTap's editor, the LangGraph SDK, pdf-lib) are not route chunks: they load on demand and are checked
// only for not being part of the initial set.
const fs = require("node:fs");
const path = require("node:path");

const DIST = path.join(__dirname, "dist");
const manifestPath = path.join(DIST, ".vite", "manifest.json");
if (!fs.existsSync(manifestPath)) {
  throw new Error(
    "dist/.vite/manifest.json is missing: build with `vite build --manifest` (pnpm --filter @naadi/web size)",
  );
}
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const html = fs.readFileSync(path.join(DIST, "index.html"), "utf8");
const inHtml = (ext) =>
  [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+)"/g)].map((m) => m[1]).filter((f) => f.endsWith(ext));

const LAZY_LIBRARIES =
  /node_modules\/(?:\.pnpm\/)?(?:@?mermaid|shiki|@shikijs|katex|pdfjs-dist|@excalidraw|@tiptap|prosemirror|@langchain|langsmith|cytoscape|elkjs)/;

// On-demand feature modules that wrap one of those libraries (loaded by a button, never by a route).
const ON_DEMAND = /^src\/pdf\/export\.ts$/; // pdf-lib + fontkit, loaded when the student exports a PDF

const routeChunks = Object.entries(manifest)
  .filter(([src, e]) => e.isDynamicEntry && src.startsWith("src/") && !LAZY_LIBRARIES.test(src) && !ON_DEMAND.test(src))
  .map(([src, e]) => ({ name: `route chunk: ${src}`, path: `dist/${e.file}`, limit: "150 KB", gzip: true }));

const initialJs = inHtml(".js");
const initialCss = inHtml(".css");
const lazyInInitial = initialJs.filter((f) =>
  Object.entries(manifest).some(([src, e]) => e.file === f && LAZY_LIBRARIES.test(src)),
);
if (lazyInInitial.length) {
  throw new Error(`lazy-only libraries are in the initial load: ${lazyInInitial.join(", ")}`);
}

module.exports = [
  {
    name: "initial JS (index.html scripts + modulepreloads)",
    path: initialJs.map((f) => `dist/${f}`),
    limit: "250 KB",
    gzip: true,
  },
  { name: "initial CSS", path: initialCss.map((f) => `dist/${f}`), limit: "40 KB", gzip: true },
  ...routeChunks,
];
