import { Dexie } from "dexie";
import { type OutboxRow, resetNaadiDbForTests } from "../offline/db";
import {
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
 * Pathway progress and cost entries written on this device and not yet confirmed by the server (segment 17). Since
 * segment 18 the entries live in the device outbox (IndexedDB `naadi-v1`, entities `user_pathway_task` and `cost`,
 * one row per (entity, key)) with every other write; the rules below are unchanged. Every write is recorded BEFORE
 * any request, so a reload or no connection loses nothing; the screens show "Saved on this device · will sync"
 * while an entry exists. Last-writer-wins per row by `client_updated_at_ms`; each request carries the row's op id as
 * `Idempotency-Key`. When another device's newer version wins, the local one is kept 7 days in the sync sheet.
 * Vault files are NEVER queued or kept here: an upload or an open needs a connection.
 *
 * `v` grows with every local change; an answer only clears the entry when no newer change arrived meanwhile.
 */

export type PathwayEntity = "user_pathway_task" | "cost";
export type OpKind = "patch" | "create" | "delete";

export interface PathwayOp {
  user: string;
  entity: PathwayEntity;
  key: string;
  v: number;
  kind: OpKind;
  /** The fields changed since the last confirmed write (a create carries them all). */
  fields: Record<string, unknown>;
  /** `client_updated_at_ms` of the latest local change. */
  stamp: number;
  queuedAt: number;
  /** The server refused this write for a reason a retry cannot fix. */
  refused: string | null;
  /** The outbox op id (sent as `Idempotency-Key`). */
  opId?: string;
}

type Stored = Pick<PathwayOp, "v" | "kind" | "fields" | "stamp">;

function fromRow(r: OutboxRow): PathwayOp {
  const b = r.body as Stored;
  return {
    user: r.user_id,
    entity: r.entity as PathwayEntity,
    key: r.key,
    v: b.v,
    kind: b.kind,
    fields: b.fields,
    stamp: b.stamp,
    queuedAt: r.queued_at,
    refused: r.state === "failed" ? (r.last_error ?? "refused") : null,
    opId: r.op_id,
  };
}

/** Creates that were sent at least once (the request may have reached the server, so a delete must follow). */
const attempted = new Set<string>();
const loaded = new Set<string>();

export function subscribePathwayOps(listener: () => void): () => void {
  return subscribeOutbox(listener);
}

export function pathwayOpsVersion(): number {
  return outboxVersion();
}

const mkey = (user: string, entity: PathwayEntity, key: string) => `${user}\u0000${entity}\u0000${key}`;

/** Test hook: forget what is in memory (as a reload would); IndexedDB keeps its rows. */
export function resetPathwayDbForTests(): void {
  loaded.clear();
  attempted.clear();
  resetOutboxForTests();
  resetNaadiDbForTests();
}

export function pendingOps(user: string): PathwayOp[] {
  return [...outboxRows(user, "user_pathway_task"), ...outboxRows(user, "cost")]
    .map(fromRow)
    .sort((a, b) => a.queuedAt - b.queuedAt);
}

export function pendingFor(user: string, entity: PathwayEntity, key: string): PathwayOp | undefined {
  const r = rowsFor(user, entity, key)[0];
  return r ? fromRow(r) : undefined;
}

/** Read this user's entries (once per page), moving the pre-segment-18 `naadi-pathway` rows in once. */
export async function loadOps(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  await loadOutbox(user);
  await migrateLegacyPathway(user);
}

/** One-time move of the segment-17 `naadi-pathway` database into the outbox; the old database is then deleted. */
export async function migrateLegacyPathway(user: string): Promise<number> {
  if (!(await outboxPersists())) return 0;
  try {
    if (!(await Dexie.exists("naadi-pathway"))) return 0;
    const old = new Dexie("naadi-pathway");
    old.version(1).stores({ ops: "[user+entity+key], user, queuedAt" });
    const mine = ((await old.table("ops").where("user").equals(user).toArray()) as PathwayOp[]).filter(
      (o) => !pendingFor(user, o.entity, o.key),
    );
    for (const o of mine) await write(o);
    await old.table("ops").where("user").equals(user).delete();
    const left = await old.table("ops").count();
    old.close();
    if (left === 0) await Dexie.delete("naadi-pathway");
    return mine.length;
  } catch {
    return 0;
  }
}

async function forget(user: string, entity: PathwayEntity, key: string): Promise<void> {
  await removeEntityRow(user, entity, key);
}

function write(op: PathwayOp): PathwayOp {
  const body: Stored = { v: op.v, kind: op.kind, fields: op.fields, stamp: op.stamp };
  void putEntityRow(op.user, op.entity, op.key, body, {
    state: op.refused ? "failed" : "queued",
    last_error: op.refused,
    method: op.kind === "delete" ? "DELETE" : op.kind === "create" ? "POST" : "PATCH",
  });
  return op;
}

/** A task's status, due date or notes changed on this device. Later changes to the same task fold into one entry. */
export function queueTask(user: string, taskId: string, fields: Record<string, unknown>, stamp: number): PathwayOp {
  const prior = pendingFor(user, "user_pathway_task", taskId);
  return write({
    user,
    entity: "user_pathway_task",
    key: taskId,
    v: (prior?.v ?? 0) + 1,
    kind: "patch",
    fields: { ...(prior?.fields ?? {}), ...fields },
    stamp: Math.max(stamp, prior?.stamp ?? 0),
    queuedAt: prior?.queuedAt ?? Date.now(),
    refused: null,
  });
}

/** A new cost entry (its id is made here, so the server treats a replay as the same entry). */
export function queueCostCreate(user: string, id: string, fields: Record<string, unknown>, stamp: number): PathwayOp {
  return write({
    user,
    entity: "cost",
    key: id,
    v: 1,
    kind: "create",
    fields,
    stamp,
    queuedAt: Date.now(),
    refused: null,
  });
}

export function queueCostUpdate(user: string, id: string, fields: Record<string, unknown>, stamp: number): PathwayOp {
  const prior = pendingFor(user, "cost", id);
  return write({
    user,
    entity: "cost",
    key: id,
    v: (prior?.v ?? 0) + 1,
    kind: prior?.kind === "create" ? "create" : "patch",
    fields: { ...(prior?.fields ?? {}), ...fields },
    stamp: Math.max(stamp, prior?.stamp ?? 0),
    queuedAt: prior?.queuedAt ?? Date.now(),
    refused: null,
  });
}

export function queueCostDelete(user: string, id: string, stamp: number): void {
  const k = mkey(user, "cost", id);
  const prior = pendingFor(user, "cost", id);
  if (prior?.kind === "create" && !attempted.has(k)) {
    // never sent: the server has never heard of it, so there is nothing to delete there
    void forget(user, "cost", id);
    return;
  }
  write({
    user,
    entity: "cost",
    key: id,
    v: (prior?.v ?? 0) + 1,
    kind: "delete",
    fields: {},
    stamp: Math.max(stamp, prior?.stamp ?? 0),
    queuedAt: prior?.queuedAt ?? Date.now(),
    refused: null,
  });
}

export type SendResult = "ok" | "unreachable" | { refused: string } | { conflict: unknown };

export interface PathwayTransport {
  patchTask: (taskId: string, fields: Record<string, unknown>, stamp: number, opId?: string) => Promise<SendResult>;
  createCost: (id: string, fields: Record<string, unknown>, stamp: number, opId?: string) => Promise<SendResult>;
  patchCost: (id: string, fields: Record<string, unknown>, stamp: number, opId?: string) => Promise<SendResult>;
  deleteCost: (id: string, stamp: number, opId?: string) => Promise<SendResult>;
}

let flushing: Promise<number> | null = null;

/** Send this user's entries in the order they were made. Returns how many were confirmed. Stops at the first
 * entry the network could not carry. */
export function flushOps(user: string, transport: PathwayTransport): Promise<number> {
  if (flushing) return flushing;
  const run = (async () => {
    let confirmed = 0;
    for (const op of pendingOps(user)) {
      if (op.refused) continue;
      const k = mkey(op.user, op.entity, op.key);
      if (op.kind === "create") attempted.add(k);
      let result: SendResult;
      if (op.entity === "user_pathway_task") result = await transport.patchTask(op.key, op.fields, op.stamp, op.opId);
      else if (op.kind === "create") result = await transport.createCost(op.key, op.fields, op.stamp, op.opId);
      else if (op.kind === "delete") result = await transport.deleteCost(op.key, op.stamp, op.opId);
      else result = await transport.patchCost(op.key, op.fields, op.stamp, op.opId);
      if (result === "unreachable") break;
      const now = pendingFor(op.user, op.entity, op.key);
      if (typeof result === "object" && "conflict" in result) {
        // Last-writer-wins: another device's newer version (or delete) stands; this one is kept in the sync sheet.
        await recordConflict(op.user, op.entity, op.key, op.fields, result.conflict);
      }
      if (result === "ok" || (typeof result === "object" && "conflict" in result)) {
        confirmed += 1;
        if (now && now.v === op.v) {
          attempted.delete(k);
          await forget(op.user, op.entity, op.key);
        } else if (now && now.kind === "create") {
          // a change arrived while the create was in flight: it is now an update of an existing entry
          write({ ...now, kind: "patch" });
        }
      } else if (now && now.v === op.v && typeof result === "object" && "refused" in result) {
        write({ ...now, refused: result.refused });
      }
    }
    return confirmed;
  })();
  // Set after the run starts, cleared when it ends: a run with nothing to await finishes before this line.
  flushing = run;
  void run.finally(() => {
    if (flushing === run) flushing = null;
  });
  return run;
}

export function discardOp(user: string, entity: PathwayEntity, key: string): void {
  attempted.delete(mkey(user, entity, key));
  void forget(user, entity, key);
}
