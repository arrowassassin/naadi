import { type InkDiff, type InkItem, InkItemsSchema } from "@naadi/shared";
import { bboxOfItem, EMPTY_RECT, type Rect, unionRect } from "./geometry";
import { GridIndex } from "./spatial";

/** An item plus its z-order key (render order). */
export interface Entry {
  item: InkItem;
  seq: number;
}

/** One user action: the entries it removed and the entries it added (same id on both sides = an update). */
export interface Change {
  before: Entry[];
  after: Entry[];
}

export type DiffListener = (diff: InkDiff) => void;
export type DirtyListener = (dirty: Rect) => void;

const HISTORY_LIMIT = 500;

/**
 * The in-memory ink layer: items by id with a z-order, a spatial index, and undo/redo of whole user
 * actions. Every committed action produces exactly one InkDiff for the consumer (persistence, sync);
 * a gesture that edits as it goes (pixel eraser) runs in a transaction so it is still one action.
 */
export class InkDocument {
  private readonly entries = new Map<string, Entry>();
  readonly index = new GridIndex();
  private nextSeq = 1;
  private undoStack: Change[] = [];
  private redoStack: Change[] = [];
  private readonly diffListeners = new Set<DiffListener>();
  private readonly dirtyListeners = new Set<DirtyListener>();
  private tx: { before: Map<string, Entry | null>; touched: Set<string> } | null = null;
  /** Bumped on every change, so renderers can cache per version. */
  version = 0;
  private readonly now: () => number;

  /** `now` stamps undo/redo re-applications (epoch ms), so they win last-writer-wins merges. */
  constructor(opts: { now?: () => number } = {}) {
    this.now = opts.now ?? Date.now;
  }

  /**
   * Undo and redo are new edits for sync: every item they bring back gets updatedAt = now, so a replica
   * that saw the intermediate state (e.g. a tombstone) does not win the merge. The history entry is
   * updated in place, so a later undo/redo removes exactly these versions.
   */
  private restamp(entries: Entry[]): Entry[] {
    const now = Math.floor(this.now());
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i] as Entry;
      entries[i] = { item: { ...e.item, updatedAt: Math.max(now, e.item.updatedAt + 1) }, seq: e.seq };
    }
    return entries;
  }

  get size(): number {
    return this.entries.size;
  }

  get inTransaction(): boolean {
    return this.tx !== null;
  }

  /** Committed diffs (one per user action, undo or redo). */
  onDiff(fn: DiffListener): () => void {
    this.diffListeners.add(fn);
    return () => this.diffListeners.delete(fn);
  }

  /** Every region that needs repainting, including transient edits inside a transaction. */
  onDirty(fn: DirtyListener): () => void {
    this.dirtyListeners.add(fn);
    return () => this.dirtyListeners.delete(fn);
  }

  get(id: string): InkItem | undefined {
    return this.entries.get(id)?.item;
  }

  entry(id: string): Entry | undefined {
    return this.entries.get(id);
  }

  /** Does any item match? (unordered, stops at the first hit) */
  some(fn: (item: InkItem) => boolean): boolean {
    for (const e of this.entries.values()) if (fn(e.item)) return true;
    return false;
  }

  /** Items in z-order. */
  items(): InkItem[] {
    return [...this.entries.values()].sort((a, b) => a.seq - b.seq).map((e) => e.item);
  }

  /** Entries whose boxes intersect `r`, in z-order. */
  query(r: Rect): Entry[] {
    const out: Entry[] = [];
    for (const id of this.index.query(r)) {
      const e = this.entries.get(id);
      if (e) out.push(e);
    }
    return out.sort((a, b) => a.seq - b.seq);
  }

  /** Replace the whole layer (initial load). Validates, drops tombstones, clears history; emits no diff. */
  load(items: readonly InkItem[]): void {
    const parsed = InkItemsSchema.parse(items);
    this.tx = null;
    this.entries.clear();
    this.index.clear();
    this.undoStack = [];
    this.redoStack = [];
    this.nextSeq = 1;
    for (const item of parsed) {
      if (item.deleted) continue;
      this.entries.set(item.id, { item, seq: this.nextSeq++ });
      this.index.insert(item.id, bboxOfItem(item));
    }
    this.version++;
    this.emitDirty({ x0: Number.NEGATIVE_INFINITY, y0: Number.NEGATIVE_INFINITY, x1: Infinity, y1: Infinity });
  }

  /** New entries for brand-new items, on top of everything. */
  fresh(items: readonly InkItem[]): Entry[] {
    return items.map((item) => ({ item, seq: this.nextSeq++ }));
  }

  /** Apply a change, record it for undo, and emit its diff. */
  commit(change: Change): InkDiff | null {
    if (this.tx) {
      this.applyInTransaction(change);
      return null;
    }
    if (change.before.length === 0 && change.after.length === 0) return null;
    const diff = this.applyRaw(change);
    this.record(change);
    this.emitDiff(diff);
    return diff;
  }

  begin(): void {
    if (this.tx) throw new Error("InkDocument: transaction already open");
    this.tx = { before: new Map(), touched: new Set() };
  }

  private applyInTransaction(change: Change): void {
    const tx = this.tx as NonNullable<typeof this.tx>;
    for (const e of [...change.before, ...change.after]) {
      const id = e.item.id;
      if (!tx.before.has(id)) tx.before.set(id, this.entries.get(id) ?? null);
      tx.touched.add(id);
    }
    this.applyRaw(change);
  }

  /** Close the transaction as ONE undoable action with ONE diff. */
  end(): InkDiff | null {
    const tx = this.tx;
    if (!tx) return null;
    this.tx = null;
    const before: Entry[] = [];
    const after: Entry[] = [];
    for (const id of tx.touched) {
      const was = tx.before.get(id) ?? null;
      const now = this.entries.get(id) ?? null;
      if (was && now && was.item === now.item) continue;
      if (was) before.push(was);
      if (now) after.push(now);
    }
    if (before.length === 0 && after.length === 0) return null;
    const change = { before, after };
    this.record(change);
    const diff = diffOf(change);
    this.emitDiff(diff);
    return diff;
  }

  /** Abandon the transaction and restore what it touched. */
  rollback(): void {
    const tx = this.tx;
    if (!tx) return;
    this.tx = null;
    const before: Entry[] = [];
    const after: Entry[] = [];
    for (const id of tx.touched) {
      const now = this.entries.get(id);
      const was = tx.before.get(id) ?? null;
      if (now) before.push(now);
      if (was) after.push(was);
    }
    this.applyRaw({ before, after });
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  undo(): InkDiff | null {
    if (this.tx) return null;
    const c = this.undoStack.pop();
    if (!c) return null;
    this.redoStack.push(c);
    const diff = this.applyRaw({ before: c.after, after: this.restamp(c.before) });
    this.emitDiff(diff);
    return diff;
  }

  redo(): InkDiff | null {
    if (this.tx) return null;
    const c = this.redoStack.pop();
    if (!c) return null;
    this.undoStack.push(c);
    const diff = this.applyRaw({ before: c.before, after: this.restamp(c.after) });
    this.emitDiff(diff);
    return diff;
  }

  private record(change: Change): void {
    this.undoStack.push(change);
    if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift();
    this.redoStack = [];
  }

  private applyRaw(change: Change): InkDiff {
    let dirty = EMPTY_RECT;
    for (const e of change.before) {
      const cur = this.entries.get(e.item.id);
      if (!cur) continue;
      dirty = unionRect(dirty, bboxOfItem(cur.item));
      this.entries.delete(e.item.id);
      this.index.remove(e.item.id);
    }
    for (const e of change.after) {
      this.entries.set(e.item.id, { item: e.item, seq: e.seq });
      if (e.seq >= this.nextSeq) this.nextSeq = Math.floor(e.seq) + 1;
      const box = bboxOfItem(e.item);
      this.index.insert(e.item.id, box);
      dirty = unionRect(dirty, box);
    }
    this.version++;
    this.emitDirty(dirty);
    return diffOf(change);
  }

  private emitDiff(diff: InkDiff): void {
    for (const fn of this.diffListeners) fn(diff);
  }

  private emitDirty(r: Rect): void {
    for (const fn of this.dirtyListeners) fn(r);
  }
}

function diffOf(change: Change): InkDiff {
  const afterIds = new Set(change.after.map((e) => e.item.id));
  const beforeIds = new Set(change.before.map((e) => e.item.id));
  return {
    added: change.after.filter((e) => !beforeIds.has(e.item.id)).map((e) => e.item),
    updated: change.after.filter((e) => beforeIds.has(e.item.id)).map((e) => e.item),
    removed: change.before.filter((e) => !afterIds.has(e.item.id)).map((e) => e.item.id),
  };
}
