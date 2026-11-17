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
import { Dexie, type EntityTable } from "dexie";

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

export interface OutboxRow extends QueuedOp {
  user: string;
}

interface SnapshotRow {
  user: string;
  key: string;
  value: unknown;
  savedAt: number;
}

type NbDb = Dexie & {
  nbOutbox: EntityTable<OutboxRow, "seq">;
  nbSnapshots: EntityTable<SnapshotRow, "key">;
};

let db: NbDb | null = null;

export function notebooksDb(): NbDb {
  if (!db) {
    const d = new Dexie("naadi-notebooks") as NbDb;
    d.version(1).stores({ nbOutbox: "seq, user", nbSnapshots: "[user+key], user" });
    db = d;
  }
  return db;
}

/** Test hook: drop the cached handle (fake-indexeddb is reset between tests). */
export function resetNotebooksDbForTests(): void {
  db?.close();
  db = null;
}

export interface OutboxStore {
  read(): Promise<QueuedOp[]>;
  /** Apply `op` with the coalescing rules, atomically. */
  add(op: NbOp, inflight: ReadonlySet<number>): Promise<void>;
  remove(seq: number): Promise<void>;
  readSnapshot<T>(key: string): Promise<T | undefined>;
  writeSnapshot(key: string, value: unknown): Promise<void>;
}

export function dexieOutbox(user: string): OutboxStore {
  const d = () => notebooksDb();
  return {
    async read() {
      const rows = await d().nbOutbox.where("user").equals(user).sortBy("seq");
      return rows.map(({ seq, op, queuedAt }) => ({ seq, op, queuedAt }));
    },
    async add(op, inflight) {
      const t = d().nbOutbox;
      await d().transaction("rw", t, async () => {
        const rows = await t.where("user").equals(user).sortBy("seq");
        const last = await t.orderBy("seq").last();
        const next = enqueue(rows, op, (last?.seq ?? 0) + 1, inflight);
        const keep = new Set(next.map((q) => q.seq));
        const drop = rows.filter((r) => !keep.has(r.seq)).map((r) => r.seq);
        if (drop.length) await t.bulkDelete(drop);
        await t.bulkPut(next.map((q) => ({ ...q, user })));
      });
    },
    async remove(seq) {
      await d().nbOutbox.delete(seq);
    },
    async readSnapshot<T>(key: string) {
      const row = await d().nbSnapshots.get([user, key] as unknown as string);
      return row?.value as T | undefined;
    },
    async writeSnapshot(key, value) {
      await d().nbSnapshots.put({ user, key, value, savedAt: Date.now() });
    },
  };
}

/** On sign-out: forget the device copies (the server has them). Unsent edits stay for their owner. */
export async function clearNotebookSnapshots(): Promise<void> {
  try {
    await notebooksDb().nbSnapshots.clear();
  } catch {
    // IndexedDB unavailable: nothing was stored
  }
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
  send: (op: NbOp) => Promise<SendOutcome>,
): Promise<{ done: NbOp[]; dropped: NbOp[]; stopped: boolean }> {
  const done: NbOp[] = [];
  const dropped: NbOp[] = [];
  for (;;) {
    const [head] = await store.read();
    if (!head) return { done, dropped, stopped: false };
    inflight.add(head.seq);
    let outcome: SendOutcome;
    try {
      outcome = await send(head.op);
    } catch {
      outcome = "retry";
    } finally {
      inflight.delete(head.seq);
    }
    if (outcome === "retry") return { done, dropped, stopped: true };
    await store.remove(head.seq);
    (outcome === "sent" ? done : dropped).push(head.op);
  }
}
