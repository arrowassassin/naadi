import { Dexie } from "dexie";
import { type OutboxRow, resetNaadiDbForTests } from "../offline/db";
import {
  importRows,
  loadOutbox,
  outboxPersists,
  outboxRows,
  outboxVersion,
  putEntityRow,
  recordConflict,
  removeEntityRow,
  resetOutboxForTests,
  rowsFor,
  subscribeOutbox,
} from "../offline/outbox";

/**
 * Notes and highlights written on this device and not yet confirmed by the server (segment 13). Since segment 18
 * the entries live in the device outbox (IndexedDB `naadi-v1`, entity `note`, one row per note) with every other
 * write, so one status line, one sync sheet and one replay schedule cover them; the rules below are unchanged.
 * Every write is recorded BEFORE any request, so a reload, a closed tab or no connection loses nothing; the screens
 * show "Saved on this device · will sync" while a note has an entry.
 *
 * One entry per note holds the latest local state of what must reach the server:
 * - `create`: the server has not confirmed the note yet; it is sent as a POST with the note's own id (made on the
 *   device), which the API treats as idempotent, so a replay after a lost answer never makes a second note.
 * - `fields`: every field changed since the last confirmed write (a create sends them all).
 * - `deleted`: delete the note. A note created and deleted before any request went out never reaches the server.
 * `v` grows with every local change; an answer only clears the entry when no newer change arrived meanwhile.
 * Each request carries the row's op id as `Idempotency-Key` and its `client_updated_at_ms` (last-writer-wins).
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
  /** The outbox op id (sent as `Idempotency-Key`) and the LWW stamp of the latest change. */
  opId?: string;
  stamp?: number;
}

type StoredOp = Pick<NoteOp, "v" | "create" | "attempted" | "deleted" | "fields">;

function fromRow(r: OutboxRow): NoteOp {
  const b = r.body as StoredOp;
  return {
    user: r.user_id,
    id: r.key,
    v: b.v,
    create: b.create,
    attempted: b.attempted,
    deleted: b.deleted,
    fields: b.fields,
    queuedAt: r.queued_at,
    refused: r.state === "failed" ? (r.last_error ?? "refused") : null,
    opId: r.op_id,
    stamp: r.client_updated_at_ms,
  };
}

function getOp(user: string, id: string): NoteOp | undefined {
  const r = rowsFor(user, "note", id)[0];
  return r ? fromRow(r) : undefined;
}

/** Test hook: forget what is in memory (as a reload would); IndexedDB keeps its rows. */
export function resetNotesDbForTests(): void {
  loaded.clear();
  resetOutboxForTests();
  resetNaadiDbForTests();
}

export function subscribeNoteOps(listener: () => void): () => void {
  return subscribeOutbox(listener);
}

export function noteOpsVersion(): number {
  return outboxVersion();
}

/** The entries of one user, oldest first. */
export function pendingOps(user: string): NoteOp[] {
  return outboxRows(user, "note")
    .map(fromRow)
    .sort((a, b) => a.queuedAt - b.queuedAt);
}

/** Read this user's entries (once per page and user), moving the pre-segment-18 `naadi-notes` rows in once. */
const loaded = new Set<string>();
export async function loadOps(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  await loadOutbox(user);
  await migrateLegacyNotes(user);
}

/** One-time move of the segment-13 `naadi-notes` database into the outbox; the old database is then deleted. */
export async function migrateLegacyNotes(user: string): Promise<number> {
  if (!(await outboxPersists())) return 0;
  try {
    if (!(await Dexie.exists("naadi-notes"))) return 0;
    const old = new Dexie("naadi-notes");
    old.version(1).stores({ noteOps: "[user+id], user, queuedAt" });
    const all = (await old.table("noteOps").toArray()) as NoteOp[];
    const mine = all.filter((o) => o.user === user && getOp(user, o.id) === undefined);
    const { uuidv7 } = await import("@naadi/shared");
    await importRows(
      mine.map((o, i) => ({
        op_id: uuidv7(),
        user_id: user,
        entity: "note",
        key: o.id,
        group: "",
        method: "POST",
        body: {
          v: o.v,
          create: o.create,
          attempted: o.attempted,
          deleted: o.deleted,
          fields: o.fields,
        } satisfies StoredOp,
        client_updated_at_ms: o.queuedAt,
        queued_at: o.queuedAt,
        seq: o.queuedAt * 1000 + i,
        rev: o.v,
        attempts: o.attempted ? 1 : 0,
        next_at: 0,
        state: o.refused ? "failed" : "queued",
        last_error: o.refused,
      })),
    );
    await old.table("noteOps").where("user").equals(user).delete();
    const left = await old.table("noteOps").count();
    old.close();
    if (left === 0) await Dexie.delete("naadi-notes");
    return mine.length;
  } catch {
    return 0;
  }
}

async function write(op: NoteOp | null, user: string, id: string): Promise<void> {
  if (!op) {
    await removeEntityRow(user, "note", id);
    return;
  }
  const body: StoredOp = {
    v: op.v,
    create: op.create,
    attempted: op.attempted,
    deleted: op.deleted,
    fields: op.fields,
  };
  await putEntityRow(user, "note", id, body, {
    state: op.refused ? "failed" : "queued",
    last_error: op.refused,
    method: op.deleted ? "DELETE" : op.create ? "POST" : "PATCH",
  });
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
  const prev = getOp(user, id);
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
  const prev = getOp(user, id);
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
export type SendResult = "ok" | "gone" | "unreachable" | { refused: string } | { conflict: unknown };

/** What a request carries besides the note: the `Idempotency-Key` and the LWW stamp of the change. */
export interface SendMeta {
  opId?: string;
  stamp?: number;
}

export interface NotesTransport {
  create(id: string, fields: NoteFields, meta?: SendMeta): Promise<SendResult>;
  update(id: string, fields: NoteFields, meta?: SendMeta): Promise<SendResult>;
  remove(id: string, meta?: SendMeta): Promise<SendResult>;
}

function meta(op: NoteOp): SendMeta {
  return { opId: op.opId, stamp: op.stamp };
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
        const sending = getOp(user, listed.id);
        if (!sending || sending.refused) continue;
        let result: SendResult;
        try {
          if (sending.deleted) {
            result = await transport.remove(sending.id, meta(sending));
            if (result === "gone") result = "ok";
          } else if (sending.create) {
            if (!sending.attempted) await write({ ...sending, attempted: true }, user, sending.id);
            result = await transport.create(sending.id, sending.fields, meta(getOp(user, sending.id) ?? sending));
          } else {
            result = await transport.update(sending.id, sending.fields, meta(sending));
          }
        } catch {
          result = "unreachable";
        }
        if (result === "unreachable") return cleared;
        const after = getOp(user, sending.id);
        if (!after) continue;
        if (typeof result === "object" && "conflict" in result) {
          // Last-writer-wins: the server kept a newer version (or a newer delete). Its copy is what the screens show
          // after the refetch; this device's version is kept for 7 days in the sync sheet, never dropped silently.
          await recordConflict(user, "note", sending.id, sending.fields, result.conflict);
          if (after.v === sending.v) {
            await write(null, user, sending.id);
            cleared += 1;
          } else {
            await write({ ...after, create: false, attempted: true }, user, sending.id);
            again = true;
          }
          continue;
        }
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
