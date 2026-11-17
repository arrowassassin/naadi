/// <reference lib="webworker" />
// The Naadi service worker, built by vite-plugin-pwa (`injectManifest`, see vite.config.ts).
// Segment 16 owns the push handlers only. Segment 18 adds caching below the marker; there is deliberately no
// fetch handler, no precache and no clientsClaim here.
import { registerPushHandlers, type WorkerScope } from "./pwa/pushHandlers";

declare const self: ServiceWorkerGlobalScope;

registerPushHandlers(self as unknown as WorkerScope);

// ── S18: caching ──
