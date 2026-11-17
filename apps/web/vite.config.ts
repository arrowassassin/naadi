import { createReadStream, readdirSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { defineConfig } from "vitest/config";
import { coreFiles, PRECACHE_BUDGET_BYTES, precached } from "./src/offline/sw/precache";

const API_TARGET = process.env.NAADI_API_ORIGIN ?? "http://localhost:7860";

/**
 * Excalidraw's hand-drawn fonts (whiteboard pages, segment 13), self-hosted: Excalidraw loads them by name at
 * run time from `window.EXCALIDRAW_ASSET_PATH` (set to `/assets/excalidraw/` in notebooks/whiteboard), so the
 * CSP keeps font-src 'self' and nothing is fetched from a CDN. The files carry content hashes in their names, so
 * they live under /assets (immutable cache). Dev serves them straight from the pinned package.
 */
const EXCALIDRAW_FONTS_URL = "/assets/excalidraw/fonts/";
function excalidrawFonts(): Plugin {
  // The package exports only its entry (dist/prod/index.js for require); the fonts sit next to it.
  const root = path.join(path.dirname(createRequire(import.meta.url).resolve("@excalidraw/excalidraw")), "fonts");
  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)],
    );
  // Two spots in the pinned 0.18.1 build are rewritten (and the build fails if they are not found, e.g. after an
  // upgrade): every font face lists esm.sh as a fallback after EXCALIDRAW_ASSET_PATH, which the CSP refuses with a
  // console error per face (Chrome checks every src when the FontFace is created), so the fallback becomes the
  // same self-hosted path; and its `unload` listener (refused by Chrome's default permissions policy, which also
  // breaks the back/forward cache) becomes `pagehide`, the event that handler is meant for (it only blurs).
  const PATCHES: { file: RegExp; from: string; to: string }[] = [
    {
      file: /dist[\\/]prod[\\/]chunk-K2UTITRG\.js$/,
      // biome-ignore lint/suspicious/noTemplateCurlyInString: the package's source text that is replaced
      from: '`https://esm.sh/${M.PKG_NAME?`${M.PKG_NAME}@${M.PKG_VERSION}`:"@excalidraw/excalidraw"}/dist/prod/`',
      to: `new URL(${JSON.stringify(EXCALIDRAW_FONTS_URL.replace(/fonts\/$/, ""))},location.href).href`,
    },
    {
      file: /dist[\\/]dev[\\/]chunk-4FTI6OG3\.js$/,
      // biome-ignore lint/suspicious/noTemplateCurlyInString: the package's source text that is replaced
      from: '`https://esm.sh/${define_import_meta_env_default.PKG_NAME ? `${define_import_meta_env_default.PKG_NAME}@${define_import_meta_env_default.PKG_VERSION}` : "@excalidraw/excalidraw"}/dist/prod/`',
      to: `new URL(${JSON.stringify(EXCALIDRAW_FONTS_URL.replace(/fonts\/$/, ""))},location.href).href`,
    },
    {
      file: /dist[\\/]prod[\\/]index\.js$/,
      from: 'Ae(window,"unload",this.onUnload,!1)',
      to: 'Ae(window,"pagehide",this.onUnload,!1)',
    },
    {
      file: /dist[\\/]dev[\\/]index\.js$/,
      from: 'addEventListener(window, "unload" /* UNLOAD */, this.onUnload, false)',
      to: 'addEventListener(window, "pagehide", this.onUnload, false)',
    },
  ];
  return {
    name: "naadi:excalidraw-fonts",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("@excalidraw/excalidraw")) return null;
      let out = code;
      for (const p of PATCHES) {
        if (!p.file.test(id.split("?")[0] ?? "")) continue;
        if (!out.includes(p.from)) this.error(`excalidraw patch not applicable to ${id}: update vite.config.ts`);
        out = out.split(p.from).join(p.to);
      }
      return out === code ? null : { code: out, map: null };
    },
    configureServer(server) {
      server.middlewares.use(EXCALIDRAW_FONTS_URL, (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? "").split("?")[0] ?? "").replace(/^\/+/, "");
        const file = path.resolve(root, rel);
        if (!file.startsWith(root + path.sep) || !statSync(file, { throwIfNoEntry: false })?.isFile()) return next();
        res.setHeader("content-type", "font/woff2");
        createReadStream(file).pipe(res);
      });
    },
    generateBundle() {
      for (const file of walk(root)) {
        if (!file.endsWith(".woff2")) continue;
        const rel = path.relative(root, file).split(path.sep).join("/");
        this.emitFile({
          type: "asset",
          fileName: `${EXCALIDRAW_FONTS_URL.slice(1)}${rel}`,
          source: readFileSync(file),
        });
      }
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    excalidrawFonts(),
    // The service worker is src/sw.ts, compiled to /sw.js: push handlers (segment 16) and caching (segment 18).
    // Precached (≤ 5 MB, checked here): the app shell and the core study screens' closures (precache.ts). Every
    // other chunk is cached on first use (caching.ts). Never: the YouTube player page, any /api response.
    // The app registers the worker itself (src/pwa/register.ts), in production only.
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectRegister: false,
      injectManifest: {
        injectionPoint: "self.__WB_MANIFEST",
        globPatterns: [
          "index.html",
          "assets/**/*.{js,mjs,css,woff2}",
          "fonts/**/*.woff2",
          "icons/*.png",
          "favicon.svg",
        ],
        globIgnores: ["assets/excalidraw/**", "sandbox/**", "**/*.map"],
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        // Only the shell and the core screens' closures (src/offline/sw/precache.ts); the rest is cached on first use.
        manifestTransforms: [
          async (entries) => {
            const file = fileURLToPath(new URL("./dist/.vite/manifest.json", import.meta.url));
            const core = coreFiles(JSON.parse(readFileSync(file, "utf8")));
            const kept = entries.filter((e) => precached(e.url, core));
            const bytes = kept.reduce((n, e) => n + e.size, 0);
            if (bytes > PRECACHE_BUDGET_BYTES)
              throw new Error(`precache ${bytes} bytes is over the ${PRECACHE_BUDGET_BYTES}-byte budget`);
            return { manifest: kept, warnings: [] };
          },
        ],
      },
      manifest: {
        name: "Naadi",
        short_name: "Naadi",
        description: "USMLE preparation for international medical graduates.",
        display: "standalone",
        start_url: "/",
        scope: "/",
        theme_color: "#050506", // --color-ground (packages/ui tokens.css)
        background_color: "#050506",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
    // dist/.vite/manifest.json: the service worker's precache list is computed from it (src/offline/sw/precache.ts).
    manifest: true,
    // Never inline assets as data: URIs; fonts must come from 'self' under the CSP.
    assetsInlineLimit: 0,
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        // The Mermaid renderer, served with its own CSP and only ever loaded in a sandboxed iframe.
        mermaid: fileURLToPath(new URL("./sandbox/mermaid.html", import.meta.url)),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
