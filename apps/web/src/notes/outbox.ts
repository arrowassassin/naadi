import { Dexie, type EntityTable } from "dexie";

/**
 * Notes and highlights written on this device and not yet confirmed by the server (segment 13), in IndexedDB
 * ("naadi-notes", Dexie, the pattern of ink/persist/db.ts). Every write is recorded here BEFORE any request, so a
 * reload, a closed tab or no connection loses nothing; the screens show "Saved on this device · will sync" while a
 * note has an entry.
 *
 * One entry per note holds the latest local state of what must reach the server:
 * - `create`: the server has not confirmed the note yet; it is sent as a POST with the note's own id (made on the
 *   device), which the API treats as idempotent, so a replay after a lost answer never makes a second note.
 * - `fields`: every field changed since the last confirmed write (a create sends them all).
 * - `deleted`: delete the note. A note created and deleted before any request went out never reaches the server.
 * `v` grows with every local change; an answer only clears the entry when no newer change arrived meanwhile.
 * Entries are never cleared on sign-out: they belong to that user and are sent when they sign in again here.
 */

export type NoteFields = Record<string, unknown>;

export interface NoteOp {
  user: string;
  id: string;
  v: number;
  create: boolean;
  /** A create request has been sent at least once (it may have reached the server). */
  attempted: boolean;
  deleted: boolean;
  fields: NoteFields;
  queuedAt: number;
  /** The server refused this write for a reason a retry cannot fix (kept so the screen can say so). */
  refused: string | null;
}

type NotesDb = Dexie & { noteOps: EntityTable<NoteOp, "id"> };

let db: NotesDb | null = null;

function notesDb(): NotesDb {
  if (!db) {
    const d = new Dexie("naadi-notes") as NotesDb;
    d.version(1).stores({ noteOps: "[user+id], user, queuedAt" });
    db = d;
  }
  return db;
}

/** Test hook: drop the cached handle (fake-indexeddb is reset between tests). */
export function resetNotesDbForTests(): void {
  db?.close();
  db = null;
  memory.clear();
  loaded.clear();
  emit();
}

/** Entries also live in memory, so a private window (no IndexedDB) still queues for the life of the page. */
const memory = new Map<string, NoteOp>();
const listeners = new Set<() => void>();
let version = 0;

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeNoteOps(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function noteOpsVersion(): number {
  return version;
}

const mkey = (user: string, id: string) => `${user}\u0000${id}`;

/** The entries of one user (memory first: it holds every entry written in this page's life). */
export function pendingOps(user: string): NoteOp[] {
  return [...memory.values()].filter((o) => o.user === user).sort((a, b) => a.queuedAt - b.queuedAt);
}

async function persist(op: NoteOp | null, user: string, id: string): Promise<void> {
  try {
    const t = notesDb().noteOps;
    if (op) await t.put(op);
    else await t.delete([user, id] as unknown as string);
  } catch {
    // IndexedDB unavailable: the entry stays in memory until the page closes
  }
}

/** Read this user's entries from IndexedDB into memory (once per page and user). */
const loaded = new Set<string>();
export async function loadOps(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  try {
    const rows = await notesDb().noteOps.where("user").equals(user).toArray();
    for (const r of rows) if (!memory.has(mkey(user, r.id))) memory.set(mkey(user, r.id), r);
    emit();
  } catch {
    // nothing stored (or no IndexedDB)
  }
}

function write(op: NoteOp | null, user: string, id: string): Promise<void> {
  if (op) memory.set(mkey(user, id), op);
  else memory.delete(mkey(user, id));
  emit();
  return persist(op, user, id);
}

let clock = 0;
/** Strictly increasing queue time, so entries made in the same millisecond keep their order. */
function now(): number {
  clock = Math.max(clock + 1, Date.now());
  return clock;
}

export function queueCreate(user: string, id: string, fields: NoteFields): Promise<void> {
  return write(
    {
      user,
      id,
      v: 1,
      create: true,
      attempted: false,
      deleted: false,
      fields: { ...fields },
      queuedAt: now(),
      refused: null,
    },
    user,
    id,
  );
}

export function queueUpdate(user: string, id: string, patch: NoteFields): Promise<void> {
  const prev = memory.get(mkey(user, id));
  if (prev?.deleted) return Promise.resolve();
  const op: NoteOp = prev
    ? { ...prev, v: prev.v + 1, fields: { ...prev.fields, ...patch }, refused: null }
    : {
        user,
        id,
        v: 1,
        create: false,
        attempted: false,
        deleted: false,
        fields: { ...patch },
        queuedAt: now(),
        refused: null,
      };
  return write(op, user, id);
}

export function queueDelete(user: string, id: string): Promise<void> {
  const prev = memory.get(mkey(user, id));
  // Made here and never sent: nothing to tell the server.
  if (prev?.create && !prev.attempted) return write(null, user, id);
  const op: NoteOp = prev
    ? { ...prev, v: prev.v + 1, deleted: true, refused: null }
    : {
        user,
        id,
        v: 1,
        create: false,
        attempted: false,
        deleted: true,
        fields: {},
        queuedAt: now(),
        refused: null,
      };
  return write(op, user, id);
}

/** Forget a refused entry (the student dismissed the message). */
export function dropOp(user: string, id: string): Promise<void> {
  return write(null, user, id);
}

/** What a send attempt came to. `gone`: the note no longer exists on the server (404). */
export type SendResult = "ok" | "gone" | "unreachable" | { refused: string };

export interface NotesTransport {
  create(id: string, fields: NoteFields): Promise<SendResult>;
  update(id: string, fields: NoteFields): Promise<SendResult>;
  remove(id: string): Promise<SendResult>;
}

const flushing = new Map<string, Promise<number>>();

/**
 * Send this user's entries in the order they were made. Stops at the first "unreachable" (the rest wait, in order);
 * a refused entry is kept with its reason (shown on screen) and the queue moves on. One flush per user at a time;
 * a call while one runs joins it and then runs once more, so nothing queued meanwhile is left behind.
 * Returns how many entries were cleared.
 */
export function flushOps(user: string, transport: NotesTransport): Promise<number> {
  const running = flushing.get(user);
  if (running) return running.then(() => flushOps(user, transport));
  const p = (async () => {
    let cleared = 0;
    // A few passes: an entry changed while its request was out is sent again in the next pass.
    for (let pass = 0; pass < 3; pass++) {
      let again = false;
      for (const listed of pendingOps(user)) {
        const sending = memory.get(mkey(user, listed.id));
        if (!sending || sending.refused) continue;
        let result: SendResult;
        try {
          if (sending.deleted) {
            result = await transport.remove(sending.id);
            if (result === "gone") result = "ok";
          } else if (sending.create) {
            if (!sending.attempted) await write({ ...sending, attempted: true }, user, sending.id);
            result = await transport.create(sending.id, sending.fields);
          } else {
            result = await transport.update(sending.id, sending.fields);
          }
        } catch {
          result = "unreachable";
        }
        if (result === "unreachable") return cleared;
        const after = memory.get(mkey(user, sending.id));
        if (!after) continue;
        if (typeof result === "object") {
          if (after.v === sending.v) await write({ ...after, refused: result.refused }, user, sending.id);
          continue;
        }
        if (after.v === sending.v || (result === "gone" && !after.create)) {
          await write(null, user, sending.id);
          cleared += 1;
        } else {
          // Confirmed, but changed again meanwhile: what is left is an update (or the delete) of that note.
          await write({ ...after, create: false, attempted: true }, user, sending.id);
          again = true;
        }
      }
      if (!again) break;
    }
    return cleared;
  })().finally(() => flushing.delete(user));
  flushing.set(user, p);
  return p;
}

/** Apply this device's unsent changes to a list from the server (or from the offline copy). */
export function overlay<T extends { id: string }>(
  items: readonly T[],
  ops: readonly NoteOp[],
  accept: (fields: NoteFields) => boolean = () => true,
): T[] {
  const byId = new Map(ops.map((o) => [o.id, o]));
  const out: T[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    seen.add(item.id);
    const op = byId.get(item.id);
    if (op?.deleted) continue;
    out.push(op ? ({ ...item, ...op.fields } as T) : item);
  }
  const created = ops
    .filter((o) => o.create && !o.deleted && !seen.has(o.id) && accept(o.fields))
    .sort((a, b) => b.queuedAt - a.queuedAt)
    .map((o) => ({ ...o.fields, id: o.id }) as unknown as T);
  return [...created, ...out];
}
