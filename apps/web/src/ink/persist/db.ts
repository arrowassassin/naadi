import type { InkItem } from "@naadi/shared";
import { Dexie, type EntityTable } from "dexie";

/**
 * Ink on this device (IndexedDB via Dexie), per user and surface:
 *
 * - `inkPending`: the latest unsent version of every item the student changed (upserts and tombstones). Written
 *   BEFORE any network call, so a reload, a closed tab or a dead battery loses nothing; deleted only when the
 *   server has confirmed that exact version. Segment 18 folds this table into the general outbox (same POST).
 * - `inkLayers`: the last merged layer from the server (items + sync cursor), so an offline reload still shows the
 *   ink that was already synced.
 *
 * Unsent ink is never cleared on sign-out (it is resent when that user signs in again on this device); the
 * snapshots are, since the server holds them.
 */
export interface PendingRow {
  user: string;
  surface: string;
  id: string;
  item: InkItem;
  queuedAt: number;
}

export interface LayerRow {
  user: string;
  surface: string;
  rev: number;
  items: InkItem[];
  savedAt: number;
}

export type InkDb = Dexie & {
  inkPending: EntityTable<PendingRow, "id">;
  inkLayers: EntityTable<LayerRow, "surface">;
};

let db: InkDb | null = null;

export function inkDb(): InkDb {
  if (!db) {
    const d = new Dexie("naadi-ink") as InkDb;
    d.version(1).stores({
      inkPending: "[user+surface+id], [user+surface]",
      inkLayers: "[user+surface], user",
    });
    db = d;
  }
  return db;
}

/** Test hook: drop the cached handle (fake-indexeddb is reset between tests). */
export function resetInkDbForTests(): void {
  db?.close();
  db = null;
}

/** Per-surface view of the two tables. Every method rejects when IndexedDB is unavailable (private mode). */
export interface PendingStore {
  readPending(): Promise<InkItem[]>;
  putPending(items: readonly InkItem[]): Promise<void>;
  /** Remove these exact versions (a newer local version of the same id stays). */
  ackPending(items: readonly InkItem[]): Promise<void>;
  readLayer(): Promise<{ rev: number; items: InkItem[] } | null>;
  writeLayer(rev: number, items: readonly InkItem[]): Promise<void>;
}

function sameVersion(a: InkItem, b: InkItem): boolean {
  return a.updatedAt === b.updatedAt && Boolean(a.deleted) === Boolean(b.deleted);
}

export function dexieStore(user: string, surface: string): PendingStore {
  const d = () => inkDb();
  return {
    async readPending() {
      const rows = await d().inkPending.where("[user+surface]").equals([user, surface]).toArray();
      return rows.map((r) => r.item);
    },
    async putPending(items) {
      if (items.length === 0) return;
      const queuedAt = Date.now();
      await d().inkPending.bulkPut(items.map((item) => ({ user, surface, id: item.id, item, queuedAt })));
    },
    async ackPending(items) {
      if (items.length === 0) return;
      const t = d().inkPending;
      await d().transaction("rw", t, async () => {
        const keys = items.map((i) => [user, surface, i.id] as unknown as string);
        const rows = await t.bulkGet(keys);
        const drop: string[] = [];
        rows.forEach((row, n) => {
          const sent = items[n];
          if (row && sent && sameVersion(row.item, sent)) drop.push(keys[n] as string);
        });
        if (drop.length) await t.bulkDelete(drop);
      });
    },
    async readLayer() {
      const row = await d().inkLayers.get([user, surface] as unknown as string);
      return row ? { rev: row.rev, items: row.items } : null;
    },
    async writeLayer(rev, items) {
      await d().inkLayers.put({ user, surface, rev, items: [...items], savedAt: Date.now() });
    },
  };
}

/** On sign-out: forget the synced snapshots (the server has them). Unsent ink stays for its owner. */
export async function clearInkSnapshots(): Promise<void> {
  try {
    await inkDb().inkLayers.clear();
  } catch {
    // IndexedDB unavailable: nothing was stored
  }
}
