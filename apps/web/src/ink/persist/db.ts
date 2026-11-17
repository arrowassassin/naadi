import type { InkItem } from "@naadi/shared";
import { Dexie, type EntityTable } from "dexie";
import { getCached, putCached } from "../../offline/cache";
import { type OutboxRow, resetNaadiDbForTests } from "../../offline/db";
import {
  loadOutbox,
  outboxPersists,
  outboxRows,
  putEntityRow,
  putEntityRows,
  removeEntityRow,
  resetOutboxForTests,
  rowsFor,
} from "../../offline/outbox";

/**
 * Ink on this device, per user and surface, in the offline database `naadi-v1` (segment 18; segment 12b kept it in
 * `naadi-ink`, migrated once):
 *
 * - pending: the latest unsent version of every item the student changed (upserts and tombstones), as outbox rows
 *   of entity `ink`. Written BEFORE any network call, so a reload, a closed tab or a dead battery loses nothing;
 *   removed only when the server has confirmed that exact version.
 * - layer: the last merged layer from the server (items + sync cursor) in the `inkLayers` cache table, so an
 *   offline reload still shows the ink that was already synced.
 * - quarantine: items the server refused as invalid, as parked outbox rows (`ink_invalid`). They don't block the
 *   rest of the queue, but they are never dropped silently: the surface shows an error, and the sync sheet lists
 *   them, until the student retries (back to pending) or discards them.
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

export interface RejectedRow {
  user: string;
  surface: string;
  id: string;
  item: InkItem;
  rejectedAt: number;
}

export type InkDb = Dexie & {
  inkPending: EntityTable<PendingRow, "id">;
  inkLayers: EntityTable<LayerRow, "surface">;
  inkRejected: EntityTable<RejectedRow, "id">;
};

/** The segment-12b database, opened only to move its rows into `naadi-v1` once (then deleted). */
function legacyInkDb(): InkDb {
  const d = new Dexie("naadi-ink") as InkDb;
  d.version(1).stores({
    inkPending: "[user+surface+id], [user+surface]",
    inkLayers: "[user+surface], user",
  });
  d.version(2).stores({
    inkPending: "[user+surface+id], [user+surface]",
    inkLayers: "[user+surface], user",
    inkRejected: "[user+surface+id], [user+surface]",
  });
  return d;
}

const migrated = new Set<string>();

/** One-time move of `naadi-ink` (pending, quarantined, snapshots) into `naadi-v1`; the old database is then deleted. */
export async function migrateLegacyInk(user: string): Promise<number> {
  if (migrated.has(user)) return 0;
  migrated.add(user);
  if (!(await outboxPersists())) return 0;
  try {
    if (!(await Dexie.exists("naadi-ink"))) return 0;
    const old = legacyInkDb();
    const pending = (await old.inkPending.toArray()).filter((r) => r.user === user);
    const rejected = (await old.inkRejected.toArray()).filter((r) => r.user === user);
    const layers = await old.inkLayers.where("user").equals(user).toArray();
    for (const r of pending) await putEntityRow(user, "ink", rowKey(r.surface, r.id), r.item, { group: r.surface });
    for (const r of rejected)
      await putEntityRow(user, "ink", rowKey(r.surface, r.id), r.item, {
        group: r.surface,
        state: "failed",
        last_error: REJECTED,
      });
    for (const l of layers) await putCached(user, "inkLayers", l.surface, { rev: l.rev, items: l.items });
    await old.inkPending.bulkDelete(pending.map((r) => [r.user, r.surface, r.id] as unknown as string));
    await old.inkRejected.bulkDelete(rejected.map((r) => [r.user, r.surface, r.id] as unknown as string));
    await old.inkLayers.where("user").equals(user).delete();
    const left = (await old.inkPending.count()) + (await old.inkRejected.count()) + (await old.inkLayers.count());
    old.close();
    if (left === 0) await Dexie.delete("naadi-ink");
    return pending.length + rejected.length;
  } catch {
    return 0;
  }
}

/** Test hook: forget what is in memory (as a reload would); IndexedDB keeps its rows. */
export function resetInkDbForTests(): void {
  resetOutboxForTests();
  resetNaadiDbForTests();
  migrated.clear();
}

/** Test helpers: what this device holds for ink (pending, quarantined, snapshots). */
export function inkRowsForTests(user?: string): { pending: InkItem[]; rejected: InkItem[] } {
  const rows = user ? outboxRows(user, "ink") : allInkRows();
  return {
    pending: rows.filter((r) => r.state !== "failed").map((r) => r.body as InkItem),
    rejected: rows.filter((r) => r.state === "failed").map((r) => r.body as InkItem),
  };
}

function allInkRows(): OutboxRow[] {
  const users = new Set<string>(knownUsers);
  return [...users].flatMap((u) => outboxRows(u, "ink"));
}

const knownUsers = new Set<string>();
const REJECTED = "ink_invalid";
const rowKey = (surface: string, id: string) => `${surface}\u0000${id}`;

/** Per-surface view of the two tables. Every method rejects when IndexedDB is unavailable (private mode). */
export interface PendingStore {
  readPending(): Promise<InkItem[]>;
  putPending(items: readonly InkItem[]): Promise<void>;
  /** Remove these exact versions (a newer local version of the same id stays). */
  ackPending(items: readonly InkItem[]): Promise<void>;
  readLayer(): Promise<{ rev: number; items: InkItem[] } | null>;
  writeLayer(rev: number, items: readonly InkItem[]): Promise<void>;
  readRejected(): Promise<InkItem[]>;
  /** Move these versions from pending to the quarantine (one transaction). */
  quarantine(items: readonly InkItem[]): Promise<void>;
  /** Remove these ids from the quarantine (retried: they are pending again; or discarded). */
  releaseRejected(ids: readonly string[]): Promise<void>;
}

function sameVersion(a: InkItem, b: InkItem): boolean {
  return a.updatedAt === b.updatedAt && Boolean(a.deleted) === Boolean(b.deleted);
}

/**
 * The surface's view of `naadi-v1` (segment 18): pending and quarantined items are outbox rows of entity `ink`
 * (one per item, grouped by surface, the quarantine as parked rows with code `ink_invalid`), the merged layer is the
 * `inkLayers` cache row. InkSync, the transport and mergeInkItems are unchanged.
 */
export function dexieStore(user: string, surface: string): PendingStore {
  knownUsers.add(user);
  const ready = async () => {
    await loadOutbox(user);
    await migrateLegacyInk(user);
  };
  const mine = () => outboxRows(user, "ink").filter((r) => r.group === surface);
  const find = (id: string) => rowsFor(user, "ink", rowKey(surface, id))[0];
  return {
    async readPending() {
      await ready();
      return mine()
        .filter((r) => r.state !== "failed")
        .map((r) => r.body as InkItem);
    },
    async putPending(items) {
      await ready();
      await putEntityRows(
        user,
        "ink",
        items.map((item) => ({ key: rowKey(surface, item.id), body: item, group: surface })),
      );
    },
    async ackPending(items) {
      for (const sent of items) {
        const row = find(sent.id);
        if (row && row.state !== "failed" && sameVersion(row.body as InkItem, sent))
          await removeEntityRow(user, "ink", row.key);
      }
    },
    async readRejected() {
      await ready();
      return mine()
        .filter((r) => r.state === "failed")
        .map((r) => r.body as InkItem);
    },
    async quarantine(items) {
      for (const sent of items) {
        const row = find(sent.id);
        if (row && sameVersion(row.body as InkItem, sent))
          await putEntityRow(user, "ink", row.key, sent, { group: surface, state: "failed", last_error: REJECTED });
        else if (!row)
          await putEntityRow(user, "ink", rowKey(surface, sent.id), sent, {
            group: surface,
            state: "failed",
            last_error: REJECTED,
          });
      }
    },
    async releaseRejected(ids) {
      for (const id of ids) {
        const row = find(id);
        if (row?.state === "failed") await removeEntityRow(user, "ink", row.key);
      }
    },
    async readLayer() {
      await ready();
      return (await getCached<{ rev: number; items: InkItem[] }>(user, "inkLayers", surface)) ?? null;
    },
    async writeLayer(rev, items) {
      await putCached(user, "inkLayers", surface, { rev, items: [...items] });
    },
  };
}

/** On sign-out: the synced snapshots go with the user's downloaded copy (offline/cache.ts clearCacheFor). */
export async function clearInkSnapshots(): Promise<void> {}
