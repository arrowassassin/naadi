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
import { FIGURE_CACHE, FIGURE_MAX_ENTRIES, isPublicFigure } from "./rules";

/**
 * The service worker's caching (segment 18), registered by sw.ts under the "S18: caching" marker (S16 owns the
 * file and its push handlers). See rules.ts for what may be cached; everything else goes to the network untouched.
 * A new version waits until the page asks (`SKIP_WAITING` after "New version ready · Reload"), so nothing reloads
 * in the middle of an exam.
 */
export function registerCaching(sw: ServiceWorkerGlobalScope, manifest: (string | PrecacheEntry)[]): void {
  // The API is never cached and never answered from a cache: requests go straight to the network from here, so
  // no other handler sees them. (WebKit reports a page's fetch cut off by a navigation as a page error when a
  // worker with a fetch handler lets it fall through; answering it here keeps the cut-off inside the worker.)
  sw.addEventListener("fetch", (event: FetchEvent) => {
    const url = new URL(event.request.url);
    if (url.origin !== sw.location.origin || !(url.pathname === "/api" || url.pathname.startsWith("/api/"))) return;
    event.stopImmediatePropagation();
    // Offline, the request is left to the browser, which fails it as a plain network error (the outbox and the
    // device copies take it from there); a worker-made failure would also be logged by WebKit as a page error.
    if (sw.navigator.onLine === false) return;
    event.respondWith(fetch(event.request));
  });
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

  sw.addEventListener("message", (event: ExtendableMessageEvent) => {
    if ((event.data as { type?: string } | null)?.type === "SKIP_WAITING") void sw.skipWaiting();
  });
}
