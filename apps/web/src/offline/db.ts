import { Dexie, type EntityTable } from "dexie";

/**
 * The one offline database of this device (segment 18): IndexedDB `naadi-v1` through Dexie. It holds two kinds of
 * data, and nothing else on the device does (the service worker caches no API response):
 *
 * - Writes the server has not confirmed: `outbox` (every entity, one row per pending op) and `conflicts` (the
 *   local version that lost a last-writer-wins race, kept 7 days so the student can still read it).
 * - The "Download this week" copy and the last good reads: `sections`, `media`, `questions`, `cards`, `notes`,
 *   `notebooks`, `inkLayers`, `planDays`, `pathway`, `reads` (what a screen last read online), plus `meta`
 *   (download state).
 *
 * Every row carries `user_id`; a signed-in user only ever reads their own rows. On sign-out the cache tables are
 * cleared, but outbox rows stay: they belong to that user and are sent when they sign in here again.
 *
 * Leak rule (review blocker): no answer, explanation, why-not or objective is ever written here before the
 * attempt synced. `questions` rows are written only through `guardQuestion` (download.ts), and a test scans every
 * table after a full download.
 */

export type OutboxState = "queued" | "sending" | "failed";

export type OutboxEntity =
  | "attempt"
  | "card_review"
  | "ink"
  | "note"
  | "notebook"
  | "plan_item"
  | "user_pathway_task"
  | "cost"
  | "reading_position";

export interface OutboxRow {
  /** uuidv7: sortable by creation, sent as the `Idempotency-Key`. */
  op_id: string;
  user_id: string;
  entity: OutboxEntity;
  /** The row the op writes (LWW entities coalesce on it). */
  key: string;
  /** A batch the entity sends together (ink: the surface). Empty for every other entity. */
  group: string;
  method: "POST" | "PUT" | "PATCH" | "DELETE";
  /** Entity-specific request (path parameters and body); only the entity's handler reads it. */
  body: unknown;
  /** Device ms adjusted to the server's clock; the LWW stamp of the op. */
  client_updated_at_ms: number;
  queued_at: number;
  /** Strictly increasing per device: FIFO order within an entity. */
  seq: number;
  /** Bumped by every local change to a queued op; an answer only clears the row when it is still the one sent. */
  rev: number;
  attempts: number;
  /** Not before this time (backoff). */
  next_at: number;
  state: OutboxState;
  /** Stable code of the last failure (`offline`, `server`, a server error code); never a sentence. */
  last_error: string | null;
}

export interface ConflictRow {
  id?: number;
  user_id: string;
  entity: OutboxEntity;
  key: string;
  /** What this device had written (it lost). */
  local: unknown;
  /** What the server kept. */
  server: unknown;
  at: number;
}

export interface CacheRow {
  /** `${user_id}\u0000${id}` */
  pk: string;
  user_id: string;
  id: string;
  data: unknown;
  /** Bytes of `data` as JSON (the Settings size line). */
  bytes: number;
  saved_at: number;
}

export interface MetaRow {
  pk: string;
  user_id: string;
  k: string;
  v: unknown;
}

export const CACHE_TABLES = [
  "sections",
  "media",
  "questions",
  "cards",
  "notes",
  "notebooks",
  "inkLayers",
  "planDays",
  "pathway",
  // The last good copy of what a screen read online (chapters, a question view, the due list), for an offline reload.
  "reads",
] as const;
export type CacheTable = (typeof CACHE_TABLES)[number];

export type NaadiDb = Dexie & {
  outbox: EntityTable<OutboxRow, "op_id">;
  conflicts: EntityTable<ConflictRow, "id">;
  meta: EntityTable<MetaRow, "pk">;
} & { [T in CacheTable]: EntityTable<CacheRow, "pk"> };

export const DB_NAME = "naadi-v1";

let db: NaadiDb | null = null;

const cacheSchema = "pk, user_id, saved_at";

export function naadiDb(): NaadiDb {
  if (!db) {
    const d = new Dexie(DB_NAME) as NaadiDb;
    d.version(1).stores({
      outbox: "op_id, user_id, [user_id+entity], [user_id+entity+key], [user_id+entity+group], seq, state",
      conflicts: "++id, user_id, at, [user_id+entity+key]",
      meta: "pk, user_id",
      ...Object.fromEntries(CACHE_TABLES.map((t) => [t, cacheSchema])),
    });
    db = d;
  }
  return db;
}

/** Test hook: drop the cached handle (fake-indexeddb is reset between tests). */
export function resetNaadiDbForTests(): void {
  db?.close();
  db = null;
}

export const pk = (user: string, id: string) => `${user}\u0000${id}`;

const writing = new Set<Promise<unknown>>();
/** Count a write in `whenPersisted` until it settles. */
export function track<T>(p: Promise<T>): Promise<T> {
  writing.add(p);
  void p.finally(() => writing.delete(p)).catch(() => undefined);
  return p;
}

/** Resolves once every write to naadi-v1 started so far has settled (tests, and before a deliberate reload). */
export async function whenPersisted(): Promise<void> {
  while (writing.size) await Promise.allSettled([...writing]);
}

export async function getMeta<T>(user: string, k: string): Promise<T | undefined> {
  try {
    const row = await naadiDb().meta.get(pk(user, k));
    return row?.v as T | undefined;
  } catch {
    return undefined;
  }
}

export async function setMeta(user: string, k: string, v: unknown): Promise<void> {
  try {
    await naadiDb().meta.put({ pk: pk(user, k), user_id: user, k, v });
  } catch {
    // IndexedDB unavailable: the value lives for this page only (callers keep their own copy)
  }
}
