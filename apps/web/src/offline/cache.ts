import { CACHE_TABLES, type CacheTable, naadiDb, pk, track } from "./db";

/**
 * The downloaded copy ("Download this week") and the last good reads, per user, in `naadi-v1`'s cache tables.
 * Reads fall back here only when the network failed (`offlineFirst`), so a stale copy never hides a fresh one.
 */
export async function putCached(user: string, table: CacheTable, id: string, data: unknown): Promise<number> {
  const json = JSON.stringify(data);
  const bytes = new TextEncoder().encode(json).length;
  try {
    await track(naadiDb()[table].put({ pk: pk(user, id), user_id: user, id, data, bytes, saved_at: Date.now() }));
  } catch {
    // IndexedDB unavailable or full: the online copy still works
  }
  return bytes;
}

export async function getCached<T>(user: string, table: CacheTable, id: string): Promise<T | undefined> {
  try {
    const row = await naadiDb()[table].get(pk(user, id));
    return row?.data as T | undefined;
  } catch {
    return undefined;
  }
}

export async function listCached<T>(user: string, table: CacheTable): Promise<T[]> {
  try {
    const rows = await naadiDb()[table].where("user_id").equals(user).toArray();
    return rows.map((r) => r.data as T);
  } catch {
    return [];
  }
}

/** Bytes this user's copy takes, per table. */
export async function cachedBytes(user: string): Promise<Record<CacheTable, number>> {
  const out = Object.fromEntries(CACHE_TABLES.map((t) => [t, 0])) as Record<CacheTable, number>;
  try {
    for (const t of CACHE_TABLES) {
      await naadiDb()
        [t].where("user_id")
        .equals(user)
        .each((r) => {
          out[t] += r.bytes;
        });
    }
  } catch {
    // nothing stored
  }
  return out;
}

/** Remove this user's downloaded copy (Settings: Remove download, or sign-out). Unsent writes are not touched. */
export async function clearCacheFor(user: string): Promise<void> {
  try {
    const d = naadiDb();
    await d.transaction(
      "rw",
      CACHE_TABLES.map((t) => d[t]),
      async () => {
        for (const t of CACHE_TABLES) await d[t].where("user_id").equals(user).delete();
      },
    );
    await d.meta.delete(pk(user, "download"));
  } catch {
    // nothing stored
  }
}
