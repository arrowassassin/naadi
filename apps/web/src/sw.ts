/// <reference lib="webworker" />
// The Naadi service worker, built by vite-plugin-pwa (`injectManifest`, see vite.config.ts).
// Segment 16 owns the push handlers; segment 18 the caching below the marker (src/offline/sw/caching.ts).

import { registerCaching } from "./offline/sw/caching";
import { registerPushHandlers, type WorkerScope } from "./pwa/pushHandlers";

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: (string | { url: string; revision: string | null })[] };

registerPushHandlers(self as unknown as WorkerScope);

// ── S18: caching ──
// Precache (app shell, chunks, fonts, icons, pdf.js worker), content-public figures CacheFirst, no /api caching.
registerCaching(self, self.__WB_MANIFEST);
