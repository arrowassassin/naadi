import { Dexie, type EntityTable } from "dexie";

/**
 * Pathway progress and cost entries written on this device and not yet confirmed by the server (segment 17), in
 * IndexedDB ("naadi-pathway", the pattern of notes/outbox.ts). Every write is recorded here BEFORE any request, so a
 * reload or no connection loses nothing; the screens show "Saved on this device · will sync" while an entry exists.
 *
 * Entities follow the S18 contract (`user_pathway_task` keyed by task id, `cost` keyed by cost id, last-writer-wins
 * per row by `client_updated_at_ms`): one entry per (entity, key) holds the latest local state, so a replay after a
 * lost answer is harmless. S18's shared outbox replaces this store; the entity names and endpoints do not change.
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
}

type PathwayDb = Dexie & { ops: EntityTable<PathwayOp, "key"> };

let db: PathwayDb | null = null;

function pathwayDb(): PathwayDb {
  if (!db) {
    const d = new Dexie("naadi-pathway") as PathwayDb;
    d.version(1).stores({ ops: "[user+entity+key], user, queuedAt" });
    db = d;
  }
  return db;
}

const memory = new Map<string, PathwayOp>();
/** Creates that were sent at least once (the request may have reached the server, so a delete must follow). */
const attempted = new Set<string>();
const loaded = new Set<string>();
const listeners = new Set<() => void>();
let version = 0;

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribePathwayOps(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function pathwayOpsVersion(): number {
  return version;
}

const mkey = (user: string, entity: PathwayEntity, key: string) => `${user}\u0000${entity}\u0000${key}`;

export function resetPathwayDbForTests(): void {
  db?.close();
  db = null;
  memory.clear();
  loaded.clear();
  emit();
}

export function pendingOps(user: string): PathwayOp[] {
  return [...memory.values()].filter((o) => o.user === user).sort((a, b) => a.queuedAt - b.queuedAt);
}

export function pendingFor(user: string, entity: PathwayEntity, key: string): PathwayOp | undefined {
  return memory.get(mkey(user, entity, key));
}

/** Read this user's entries from IndexedDB once per page (memory already holds everything written since). */
export async function loadOps(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  try {
    const rows = await pathwayDb().ops.where("user").equals(user).toArray();
    for (const r of rows) {
      const k = mkey(r.user, r.entity, r.key);
      if (!memory.has(k)) memory.set(k, r);
    }
    if (rows.length) emit();
  } catch {
    // no IndexedDB (a private window): entries live in memory for the life of the page
  }
}

async function persist(op: PathwayOp): Promise<void> {
  try {
    await pathwayDb().ops.put(op);
  } catch {
    // memory still holds it
  }
}

async function forget(user: string, entity: PathwayEntity, key: string): Promise<void> {
  try {
    await pathwayDb().ops.delete([user, entity, key] as never);
  } catch {
    // nothing to forget
  }
}

function write(op: PathwayOp): PathwayOp {
  memory.set(mkey(op.user, op.entity, op.key), op);
  void persist(op);
  emit();
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
  const prior = memory.get(k);
  if (prior?.kind === "create" && !attempted.has(k)) {
    // never sent: the server has never heard of it, so there is nothing to delete there
    memory.delete(k);
    void forget(user, "cost", id);
    emit();
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

export type SendResult = "ok" | "unreachable" | { refused: string };

export interface PathwayTransport {
  patchTask: (taskId: string, fields: Record<string, unknown>, stamp: number) => Promise<SendResult>;
  createCost: (id: string, fields: Record<string, unknown>, stamp: number) => Promise<SendResult>;
  patchCost: (id: string, fields: Record<string, unknown>, stamp: number) => Promise<SendResult>;
  deleteCost: (id: string, stamp: number) => Promise<SendResult>;
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
      if (op.entity === "user_pathway_task") result = await transport.patchTask(op.key, op.fields, op.stamp);
      else if (op.kind === "create") result = await transport.createCost(op.key, op.fields, op.stamp);
      else if (op.kind === "delete") result = await transport.deleteCost(op.key, op.stamp);
      else result = await transport.patchCost(op.key, op.fields, op.stamp);
      if (result === "unreachable") break;
      const now = memory.get(k);
      if (result === "ok") {
        confirmed += 1;
        if (now && now.v === op.v) {
          memory.delete(k);
          attempted.delete(k);
          await forget(op.user, op.entity, op.key);
        } else if (now && now.kind === "create") {
          // a change arrived while the create was in flight: it is now an update of an existing entry
          write({ ...now, kind: "patch" });
        }
        emit();
      } else if (now && now.v === op.v) {
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
  memory.delete(mkey(user, entity, key));
  attempted.delete(mkey(user, entity, key));
  void forget(user, entity, key);
  emit();
}
