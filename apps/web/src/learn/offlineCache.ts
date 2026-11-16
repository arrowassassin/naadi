/**
 * The last good course map and chapters per user, in the Cache Storage API (chapters are hundreds of KB, too
 * big for localStorage). Used only when the network fails, so an offline reload still shows what was read.
 * Keys are per user; everything is deleted on sign-out or a user change. The full "download this week"
 * offline mode (Dexie, outbox) is segment 18.
 */
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

export async function writeOffline(userId: string, kind: string, id: string, value: unknown): Promise<void> {
  if (!available()) return;
  try {
    const body = JSON.stringify(value);
    await (await caches.open(CACHE)).put(
      keyFor(userId, kind, id),
      new Response(body, { headers: { "content-type": "application/json" } }),
    );
  } catch {
    // quota exceeded or storage disabled: only the offline fallback is lost
  }
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
