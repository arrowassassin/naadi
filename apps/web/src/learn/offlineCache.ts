/**
 * The last good course map and chapters per user, in the Cache Storage API (chapters are hundreds of KB, too
 * big for localStorage). Used only when the network fails, so an offline reload still shows what was read.
 * Keys are per user; everything is deleted on sign-out or a user change. The full "download this week"
 * offline mode (Dexie, outbox) is segment 18.
 */
import { useSyncExternalStore } from "react";
import { onAuthChange } from "../auth/session";

const CACHE = "naadi-reader-v1";
const BASE = "https://naadi-reader.invalid";

function available(): boolean {
  return typeof caches !== "undefined" && typeof caches.open === "function";
}

function keyFor(userId: string, kind: string, id: string): string {
  return `${BASE}/${encodeURIComponent(userId)}/${kind}/${encodeURIComponent(id)}`;
}

export async function readOffline<T>(userId: string, kind: string, id: string): Promise<T | undefined> {
  if (!available()) return undefined;
  try {
    const res = await (await caches.open(CACHE)).match(keyFor(userId, kind, id));
    return res ? ((await res.json()) as T) : undefined;
  } catch {
    return undefined;
  }
}

// Cache writes still in flight. The reader and the course map count them in their idle signal, so a test
// (or a prefetching router) can tell when nothing would be cut off by navigating away.
let pending = 0;
const listeners = new Set<() => void>();
function setPending(delta: number): void {
  pending += delta;
  for (const l of listeners) l();
}

export function pendingOfflineWrites(): number {
  return pending;
}

export function subscribeOfflineWrites(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** True while no offline cache write is in flight (part of the page's idle signal). */
export function useOfflineWritesIdle(): boolean {
  return useSyncExternalStore(
    subscribeOfflineWrites,
    () => pending === 0,
    () => true,
  );
}

/**
 * Store `value` for an offline reload. Never rejects: the handler is attached synchronously to the whole
 * open-then-put chain, so a put cut off by navigation (WebKit: "Context is stopped") is never unhandled.
 */
export function writeOffline(userId: string, kind: string, id: string, value: unknown): Promise<void> {
  if (!available()) return Promise.resolve();
  let body: string;
  try {
    body = JSON.stringify(value);
  } catch {
    return Promise.resolve();
  }
  setPending(1);
  return caches
    .open(CACHE)
    .then((cache) =>
      cache.put(keyFor(userId, kind, id), new Response(body, { headers: { "content-type": "application/json" } })),
    )
    .catch(() => {
      // quota exceeded, storage disabled or the page is unloading: only the offline fallback is lost
    })
    .finally(() => setPending(-1));
}

export async function clearOffline(): Promise<void> {
  if (!available()) return;
  try {
    await caches.delete(CACHE);
  } catch {
    // ignore
  }
}

let installed = false;
export function installOfflineCacheReset(): void {
  if (installed) return;
  installed = true;
  onAuthChange((event, prev, next) => {
    if (event === "SIGNED_OUT" || (prev && next && prev !== next)) void clearOffline();
  });
}
