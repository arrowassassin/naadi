/**
 * Excalidraw reads `window.EXCALIDRAW_ASSET_PATH` to find its hand-drawn fonts. They are self-hosted under
 * /assets/excalidraw/fonts/ (vite.config.ts, `excalidrawFonts`), so the CSP keeps font-src 'self' and no CDN is
 * contacted. Excalidraw builds its font URLs when its module is evaluated, so this must run BEFORE that module
 * loads: call `ensureExcalidrawAssetPath()` right before the dynamic `import()` of the editor (a static import
 * order is not enough: the bundler evaluates an imported chunk before the importing chunk's own code).
 */
declare global {
  interface Window {
    EXCALIDRAW_ASSET_PATH?: string | string[];
  }
}

export const EXCALIDRAW_ASSET_PATH = `${import.meta.env.BASE_URL.replace(/\/+$/, "")}/assets/excalidraw/`;

export function ensureExcalidrawAssetPath(): void {
  if (typeof window !== "undefined") window.EXCALIDRAW_ASSET_PATH = EXCALIDRAW_ASSET_PATH;
}
