/**
 * Notebook edits made on this device that the server has not confirmed yet (segment 13 offline: "will sync").
 * Every write goes here first, in order, and is replayed by `flushOutbox`; ids are made on the device, so the
 * API's idempotent creates make a replay safe. The coalescing rules keep the queue short: a patch folds into a
 * queued create or patch of the same note, a delete cancels everything queued for its target, and a newer
 * page order replaces an older one. A row being sent is never folded into (its request is already on the wire).
 *
 * The queue itself (`enqueue`, pure) is separate from where it is kept (`OutboxStore`: IndexedDB through Dexie,
 * database `naadi-notebooks`; segment 18 folds it into the general outbox) so the rules are unit-tested alone.
 */
import type { NotebookCreate, NotebookPatch, NotePatch, PageCreate } from "@naadi/shared";
import { Dexie } from "dexie";
import { getCached, putCached } from "../offline/cache";
import { resetNaadiDbForTests } from "../offline/db";
import {
  loadOutbox,
  nextSeq,
  outboxPersists,
  outboxRows,
  putEntityRow,
  removeEntityRow,
  resetOutboxForTests,
  rowsFor,
} from "../offline/outbox";

export type NbOp =
  | { kind: "createNotebook"; id: string; body: NotebookCreate & { id: string; first_page_id: string } }
  | { kind: "patchNotebook"; id: string; patch: NotebookPatch }
  | { kind: "deleteNotebook"; id: string }
  | { kind: "createPage"; id: string; notebookId: string; body: PageCreate & { id: string } }
  | { kind: "patchNote"; id: string; notebookId: string; patch: NotePatch }
  | { kind: "deleteNote"; id: string; notebookId: string }
  | { kind: "reorder"; notebookId: string; pageIds: string[] };

export interface QueuedOp {
  seq: number;
  op: NbOp;
  queuedAt: number;
  /** The outbox op id, sent as `Idempotency-Key`. */
  opId?: string;
}

/** The notebook an op belongs to (for reads that overlay the queue on one notebook). */
export function opNotebook(op: NbOp): string {
  return op.kind === "createNotebook" || op.kind === "patchNotebook" || op.kind === "deleteNotebook"
    ? op.id
    : op.notebookId;
}

const PAGE_FIELDS = ["title", "paper", "canvas", "body"] as const;

/**
 * Add `op` to `queue` (ordered by seq). Returns the new queue; `nextSeq` is the seq a new row gets. Rows whose
 * seq is in `inflight` are never changed or removed here.
 */
export function enqueue(
  queue: readonly QueuedOp[],
  op: NbOp,
  nextSeq: number,
  inflight: ReadonlySet<number> = new Set(),
  now = Date.now(),
): QueuedOp[] {
  const out = queue.map((q) => ({ ...q }));
  const idle = (q: QueuedOp) => !inflight.has(q.seq);
  const add = () => out.push({ seq: nextSeq, op, queuedAt: now });

  switch (op.kind) {
    case "createNotebook":
    case "createPage":
      add();
      return out;
    case "patchNotebook": {
      const target = [...out]
        .reverse()
        .find((q) => idle(q) && (q.op.kind === "createNotebook" || q.op.kind === "patchNotebook") && q.op.id === op.id);
      if (target?.op.kind === "createNotebook") {
        const { title, cover, paper } = op.patch;
        target.op = {
          ...target.op,
          body: {
            ...target.op.body,
            ...(title != null ? { title } : {}),
            ...(cover !== undefined ? { cover } : {}),
            ...(paper != null ? { paper } : {}),
          },
        };
      } else if (target?.op.kind === "patchNotebook")
        target.op = { ...target.op, patch: { ...target.op.patch, ...op.patch } };
      else add();
      return out;
    }
    case "deleteNotebook": {
      const createdHere = out.some((q) => q.op.kind === "createNotebook" && q.op.id === op.id);
      const createSending = out.some((q) => !idle(q) && q.op.kind === "createNotebook" && q.op.id === op.id);
      const kept = out.filter((q) => !idle(q) || opNotebook(q.op) !== op.id);
      if (createdHere && !createSending) return kept; // never reached the server: nothing to delete there
      kept.push({ seq: nextSeq, op, queuedAt: now });
      return kept;
    }
    case "patchNote": {
      const target = [...out]
        .reverse()
        .find((q) => idle(q) && (q.op.kind === "createPage" || q.op.kind === "patchNote") && q.op.id === op.id);
      if (target?.op.kind === "createPage") {
        const fields: Partial<Omit<PageCreate, "id">> = {};
        for (const f of PAGE_FIELDS) if (f in op.patch) (fields as Record<string, unknown>)[f] = op.patch[f];
        const rest = Object.fromEntries(
          Object.entries(op.patch).filter(([k]) => !(PAGE_FIELDS as readonly string[]).includes(k)),
        );
        target.op = { ...target.op, body: { ...target.op.body, ...fields, id: target.op.body.id } };
        // Fields a create cannot carry (sort, anchors) still go as a patch after it.
        if (Object.keys(rest).length)
          out.push({ seq: nextSeq, op: { ...op, patch: rest as NotePatch }, queuedAt: now });
      } else if (target?.op.kind === "patchNote")
        target.op = { ...target.op, patch: { ...target.op.patch, ...op.patch } };
      else add();
      return out;
    }
    case "deleteNote": {
      const createdHere = out.some((q) => q.op.kind === "createPage" && q.op.id === op.id);
      const createSending = out.some((q) => !idle(q) && q.op.kind === "createPage" && q.op.id === op.id);
      const kept = out
        .filter((q) => !idle(q) || !((q.op.kind === "createPage" || q.op.kind === "patchNote") && q.op.id === op.id))
        .map((q) =>
          idle(q) && q.op.kind === "reorder" && q.op.notebookId === op.notebookId
            ? { ...q, op: { ...q.op, pageIds: q.op.pageIds.filter((id) => id !== op.id) } }
            : q,
        );
      if (createdHere && !createSending) return kept;
      kept.push({ seq: nextSeq, op, queuedAt: now });
      return kept;
    }
    case "reorder": {
      const kept = out.filter((q) => !(idle(q) && q.op.kind === "reorder" && q.op.notebookId === op.notebookId));
      kept.push({ seq: nextSeq, op, queuedAt: now });
      return kept;
    }
  }
}

// ── Storage ──

/**
 * Since segment 18 the queue lives in the device outbox (IndexedDB `naadi-v1`, entity `notebook`, one row per
 * queued op, FIFO by seq) and the snapshots in its `notebooks` cache table. An op the server refuses for good is
 * parked there (visible in the sync sheet with Retry / Discard), never dropped silently.
 */
const NB = "notebook" as const;

function queued(user: string): QueuedOp[] {
  return outboxRows(user, NB)
    .filter((r) => r.state !== "failed")
    .map((r) => ({ seq: r.seq, op: (r.body as { op: NbOp }).op, queuedAt: r.queued_at, opId: r.op_id }));
}

/** Test hook: forget what is in memory (as a reload would); IndexedDB keeps its rows. */
export function resetNotebooksDbForTests(): void {
  resetOutboxForTests();
  resetNaadiDbForTests();
  migrated.clear();
}

export interface OutboxStore {
  read(): Promise<QueuedOp[]>;
  /** Apply `op` with the coalescing rules, atomically. */
  add(op: NbOp, inflight: ReadonlySet<number>): Promise<void>;
  remove(seq: number): Promise<void>;
  /** Keep an op the server refused for good, visible, out of the queue's way. */
  park?(seq: number, code: string): Promise<void>;
  readSnapshot<T>(key: string): Promise<T | undefined>;
  writeSnapshot(key: string, value: unknown): Promise<void>;
}

const migrated = new Set<string>();

export function dexieOutbox(user: string): OutboxStore {
  let chain: Promise<unknown> = Promise.resolve();
  const serial = <T>(fn: () => Promise<T>): Promise<T> => {
    const p = chain.then(fn, fn);
    chain = p.catch(() => undefined);
    return p;
  };
  const ready = async () => {
    await loadOutbox(user);
    if (!migrated.has(user)) {
      migrated.add(user);
      await migrateLegacyNotebooks(user);
    }
  };
  return {
    async read() {
      await ready();
      return queued(user);
    },
    add(op, inflight) {
      return serial(async () => {
        await ready();
        const rows = queued(user);
        const next = enqueue(rows, op, nextSeq(), inflight);
        const keep = new Set(next.map((q) => q.seq));
        for (const r of rows) if (!keep.has(r.seq)) await removeEntityRow(user, NB, String(r.seq));
        for (const q of next)
          await putEntityRow(user, NB, String(q.seq), { op: q.op }, { seq: q.seq, group: opNotebook(q.op) });
      });
    },
    async remove(seq) {
      await removeEntityRow(user, NB, String(seq));
    },
    async park(seq, code) {
      const row = rowsFor(user, NB, String(seq))[0];
      if (row) await putEntityRow(user, NB, row.key, row.body, { seq, state: "failed", last_error: code });
    },
    async readSnapshot<T>(key: string) {
      return getCached<T>(user, "notebooks", key);
    },
    async writeSnapshot(key, value) {
      await putCached(user, "notebooks", key, value);
    },
  };
}

/** One-time move of the segment-13 `naadi-notebooks` queue into the outbox; the old database is then deleted. */
export async function migrateLegacyNotebooks(user: string): Promise<number> {
  if (!(await outboxPersists())) return 0;
  try {
    if (!(await Dexie.exists("naadi-notebooks"))) return 0;
    const old = new Dexie("naadi-notebooks");
    old.version(1).stores({ nbOutbox: "seq, user", nbSnapshots: "[user+key], user" });
    const rows = (await old.table("nbOutbox").where("user").equals(user).sortBy("seq")) as (QueuedOp & {
      user: string;
    })[];
    const base = nextSeq();
    for (const [i, r] of rows.entries())
      await putEntityRow(user, NB, String(base + i), { op: r.op }, { seq: base + i, group: opNotebook(r.op) });
    await old.table("nbOutbox").where("user").equals(user).delete();
    await old.table("nbSnapshots").where("user").equals(user).delete();
    const left = (await old.table("nbOutbox").count()) + (await old.table("nbSnapshots").count());
    old.close();
    if (left === 0) await Dexie.delete("naadi-notebooks");
    return rows.length;
  } catch {
    return 0;
  }
}

/** On sign-out: forget the device copies (the server has them). Unsent edits stay for their owner. */
export async function clearNotebookSnapshots(): Promise<void> {
  // The cache tables are cleared per user by the offline layer (offline/cache.ts clearCacheFor) on sign-out.
}

// ── Replay ──

/** What the server said about one op: done (or moot), refused for good, or not reachable (try later). */
export type SendOutcome = "sent" | "dropped" | "retry";

/**
 * Send the queue in order, one op at a time. A "retry" stops the replay (order matters: a page must exist before
 * its patch); "sent" and "dropped" remove the row. Returns the ops that left the queue.
 */
export async function flushOutbox(
  store: OutboxStore,
  inflight: Set<number>,
  send: (op: NbOp, opId?: string) => Promise<SendOutcome>,
): Promise<{ done: NbOp[]; dropped: NbOp[]; stopped: boolean }> {
  const done: NbOp[] = [];
  const dropped: NbOp[] = [];
  for (;;) {
    const [head] = await store.read();
    if (!head) return { done, dropped, stopped: false };
    inflight.add(head.seq);
    let outcome: SendOutcome;
    try {
      outcome = await send(head.op, head.opId);
    } catch {
      outcome = "retry";
    } finally {
      inflight.delete(head.seq);
    }
    if (outcome === "retry") return { done, dropped, stopped: true };
    if (outcome === "dropped" && store.park) await store.park(head.seq, "notebook_refused");
    else await store.remove(head.seq);
    (outcome === "sent" ? done : dropped).push(head.op);
  }
}
