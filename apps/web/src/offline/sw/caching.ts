/// <reference lib="webworker" />
import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { ExpirationPlugin } from "workbox-expiration";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  type PrecacheEntry,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { CacheFirst } from "workbox-strategies";
import { ASSET_CACHE, ASSET_MAX_ENTRIES, FIGURE_CACHE, FIGURE_MAX_ENTRIES, isAppAsset, isPublicFigure } from "./rules";

/**
 * The service worker's caching (segment 18), registered by sw.ts under the "S18: caching" marker (S16 owns the
 * file and its push handlers). See rules.ts for what may be cached; everything else goes to the network untouched.
 * A new version waits until the page asks (`SKIP_WAITING` after "New version ready · Reload"), so nothing reloads
 * in the middle of an exam.
 */
export function registerCaching(sw: ServiceWorkerGlobalScope, manifest: (string | PrecacheEntry)[]): void {
  cleanupOutdatedCaches();
  precacheAndRoute(manifest);

  registerRoute(
    new NavigationRoute(createHandlerBoundToURL("/index.html"), {
      allowlist: [/^\/(?!api(\/|$)).*/],
    }),
  );
  registerRoute(
    ({ url, request }) => request.method === "GET" && isPublicFigure(url),
    new CacheFirst({
      cacheName: FIGURE_CACHE,
      plugins: [
        new CacheableResponsePlugin({ statuses: [0, 200] }),
        new ExpirationPlugin({ maxEntries: FIGURE_MAX_ENTRIES, purgeOnQuotaError: true }),
      ],
    }),
  );

  // Chunks and assets outside the precache (admin, authoring, pathway, the PDF viewer, diagram engines, the
  // whiteboard): cached the first time a screen uses them, least recently used dropped past the cap. Precached
  // files are answered by the precache route above, which matches first.
  registerRoute(
    ({ url, request }) => request.method === "GET" && isAppAsset(url, sw.location.origin),
    new CacheFirst({
      cacheName: ASSET_CACHE,
      plugins: [
        new CacheableResponsePlugin({ statuses: [200] }),
        new ExpirationPlugin({ maxEntries: ASSET_MAX_ENTRIES, purgeOnQuotaError: true }),
      ],
    }),
  );

  sw.addEventListener("message", (event: ExtendableMessageEvent) => {
    if ((event.data as { type?: string } | null)?.type === "SKIP_WAITING") void sw.skipWaiting();
  });
}
