/**
 * The last good copy of what a screen read online (course map, chapters, a question view, the due list, notes),
 * per user, for an offline reload. Since segment 18 it lives in IndexedDB (`naadi-v1.reads`, Dexie) with the rest
 * of the offline data; the service worker caches no API response. Used only when the network fails, so a stale
 * copy never hides a fresh one. Every value passes the leak guard (no answer or explanation before an attempt).
 * Everything is deleted on sign-out or a user change.
 */
import { useSyncExternalStore } from "react";
import { onAuthChange } from "../auth/session";
import { naadiDb, pk } from "../offline/db";
import { stripHidden } from "../offline/guard";

const id = (kind: string, key: string) => `${kind}\u0000${key}`;

export async function readOffline<T>(userId: string, kind: string, key: string): Promise<T | undefined> {
  try {
    const row = await naadiDb().reads.get(pk(userId, id(kind, key)));
    return row ? (row.data as T) : undefined;
  } catch {
    return undefined;
  }
}

// Writes still in flight. The reader and the course map count them in their idle signal, so a test (or a
// prefetching router) can tell when nothing would be cut off by navigating away.
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

/** True while no offline write is in flight (part of the page's idle signal). */
export function useOfflineWritesIdle(): boolean {
  return useSyncExternalStore(
    subscribeOfflineWrites,
    () => pending === 0,
    () => true,
  );
}

/** Store `value` for an offline reload. Never rejects (quota, storage disabled, a page unloading). */
export function writeOffline(userId: string, kind: string, key: string, value: unknown): Promise<void> {
  let data: unknown;
  let bytes: number;
  try {
    data = stripHidden(value);
    bytes = JSON.stringify(data).length;
  } catch {
    return Promise.resolve();
  }
  setPending(1);
  return Promise.resolve()
    .then(() =>
      naadiDb().reads.put({
        pk: pk(userId, id(kind, key)),
        user_id: userId,
        id: id(kind, key),
        data,
        bytes,
        saved_at: Date.now(),
      }),
    )
    .then(() => undefined)
    .catch(() => {
      // only the offline fallback is lost
    })
    .finally(() => setPending(-1));
}

/** Delete every user's last-good copy (and the pre-segment-18 Cache Storage copy, which held the same data). */
export async function clearOffline(): Promise<void> {
  try {
    await naadiDb().reads.clear();
  } catch {
    // nothing stored
  }
  await dropLegacyReaderCache();
}

/** The reader's old Cache Storage copy (segment 06): data only, never the student's work, so it is just removed. */
export async function dropLegacyReaderCache(): Promise<void> {
  try {
    if (typeof caches !== "undefined") await caches.delete("naadi-reader-v1");
  } catch {
    // no Cache Storage
  }
}

let installed = false;
export function installOfflineCacheReset(): void {
  if (installed) return;
  installed = true;
  void dropLegacyReaderCache();
  onAuthChange((event, prev, next) => {
    if (event === "SIGNED_OUT" || (prev && next && prev !== next)) void clearOffline();
  });
}
