import { type InkDiff, type InkItem, InkItemsSchema } from "@naadi/shared";
import { type InkAnchorResolver, resolveAnchor, stampAnchor } from "./anchors";
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

  /** Reflow anchoring (12b): stamps anchors on edits, places anchored items for the current layout. */
  private anchors: InkAnchorResolver | null;

  /** `now` stamps undo/redo re-applications (epoch ms), so they win last-writer-wins merges. */
  constructor(opts: { now?: () => number; anchors?: InkAnchorResolver | null } = {}) {
    this.now = opts.now ?? Date.now;
    this.anchors = opts.anchors ?? null;
  }

  /** Replace the anchor resolver (e.g. after the content's blocks are measured); call reanchor() to re-place. */
  setAnchors(anchors: InkAnchorResolver | null): void {
    this.anchors = anchors;
  }

  /** Every item the user's edit produced gets the anchor of the block under its top edge (or loses a stale one). */
  private stamp(change: Change): Change {
    if (!this.anchors) return change;
    const after = change.after.map((e) => {
      const item = stampAnchor(e.item, this.anchors);
      return item === e.item ? e : { item, seq: e.seq };
    });
    return { before: change.before, after };
  }

  /**
   * Re-place anchored items for the current layout (after the content reflowed). Same ids, anchors and
   * updatedAt, so nothing is emitted, nothing is recorded and sync sees no change.
   */
  reanchor(): void {
    if (!this.anchors || this.tx) return;
    let dirty = EMPTY_RECT;
    for (const [id, e] of this.entries) {
      const item = resolveAnchor(e.item, this.anchors);
      if (item === e.item) continue;
      dirty = unionRect(unionRect(dirty, bboxOfItem(e.item)), bboxOfItem(item));
      this.entries.set(id, { item, seq: e.seq });
      this.index.remove(id);
      this.index.insert(id, bboxOfItem(item));
    }
    if (dirty === EMPTY_RECT) return;
    this.version++;
    this.emitDirty(dirty);
  }

  /**
   * Merge a sync replica's items into the live layer (12b): per id the incoming version wins when it is newer
   * (higher updatedAt; on a tie a tombstone beats a live item), the order of mergeInkItems. On a full tie the local
   * copy stays (it may differ only by anchor placement). A tombstone removes; an unknown live item is added on top.
   * A live item this layer deleted at or after the incoming version is not resurrected.
   *
   * It is not a user action: no undo step, history untouched, no onDiff. Undo/redo after it still re-stamp what
   * they bring back strictly newer than every incoming version, so the local action wins (as for any replica).
   * Returns what changed, or null.
   *
   * `authoritative`: the server's stored copy of items this device just sent, whose stamp the server changed (a
   * clock running ahead was clamped). Each replaces the local version whatever its updatedAt, and the replica stamp
   * drops to it, so a later deletion on another device wins as it should. Only for versions of this device's own
   * last edit (sync checks that nothing newer is pending).
   */
  applyRemote(items: readonly InkItem[], opts: { authoritative?: boolean } = {}): InkDiff | null {
    if (this.tx) throw new Error("InkDocument: applyRemote during a transaction");
    const before: Entry[] = [];
    const after: Entry[] = [];
    const seen = new Set<string>();
    const adopted = new Map<string, number>();
    for (const raw of items) {
      if (seen.has(raw.id)) continue; // callers pass one version per id (mergeInkItems output); keep the first
      seen.add(raw.id);
      const cur = this.entries.get(raw.id);
      const stamp = this.replicaStamp.get(raw.id);
      if (opts.authoritative) {
        adopted.set(raw.id, raw.updatedAt);
        if (cur && cur.item.updatedAt === raw.updatedAt && !!cur.item.deleted === !!raw.deleted) continue;
        if (cur) before.push(cur);
        if (!raw.deleted) after.push({ item: raw, seq: cur ? cur.seq : this.nextSeq++ });
        continue;
      }
      this.replicaStamp.set(raw.id, Math.max(stamp ?? 0, raw.updatedAt));
      if (cur) {
        const a = cur.item;
        const wins = raw.updatedAt > a.updatedAt || (raw.updatedAt === a.updatedAt && !!raw.deleted && !a.deleted);
        if (!wins) continue;
        before.push(cur);
        if (!raw.deleted) after.push({ item: raw, seq: cur.seq });
      } else if (!raw.deleted) {
        // Deleted here at or after this version (a pending tombstone ties or beats it): stay deleted.
        if (stamp !== undefined && stamp >= raw.updatedAt) continue;
        after.push({ item: raw, seq: this.nextSeq++ });
      }
    }
    if (before.length === 0 && after.length === 0) {
      for (const [id, s] of adopted) this.replicaStamp.set(id, s);
      return null;
    }
    // applyRaw bumps replicaStamp for removals the way a local delete would; that only makes later undo stamps
    // larger, never smaller than what a replica holds.
    const diff = this.applyRaw({ before, after });
    for (const [id, s] of adopted) this.replicaStamp.set(id, s);
    return diff;
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
      // Strictly newer than anything a replica can hold for this id, even within the same millisecond.
      const seen = this.replicaStamp.get(e.item.id) ?? e.item.updatedAt;
      const updatedAt = Math.max(now, seen + 1, e.item.updatedAt + 1);
      entries[i] = { item: { ...e.item, updatedAt }, seq: e.seq };
    }
    return entries;
  }

  /**
   * The newest updatedAt a sync replica can hold per id: the item's own stamp, or for a removal the
   * tombstone recordInkDiff writes (max(now, updatedAt + 1)).
   */
  private readonly replicaStamp = new Map<string, number>();

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
    for (const raw of parsed) {
      if (raw.deleted) continue;
      const item = resolveAnchor(raw, this.anchors);
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
  commit(input: Change): InkDiff | null {
    const change = this.stamp(input);
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
    const now = Math.floor(this.now());
    const kept = new Set(change.after.map((e) => e.item.id));
    for (const e of change.before) {
      const cur = this.entries.get(e.item.id);
      if (!cur) continue;
      const prev = this.replicaStamp.get(e.item.id) ?? 0;
      const stamp = kept.has(e.item.id) ? cur.item.updatedAt : Math.max(now, cur.item.updatedAt + 1);
      this.replicaStamp.set(e.item.id, Math.max(prev, stamp));
      dirty = unionRect(dirty, bboxOfItem(cur.item));
      this.entries.delete(e.item.id);
      this.index.remove(e.item.id);
    }
    for (let i = 0; i < change.after.length; i++) {
      // Anchored items land where their block is in the current layout (undo/redo/remote bring back versions
      // placed for another layout); fresh edits are already in place, so this is a no-op for them.
      const placed = change.after[i] as Entry;
      const item = resolveAnchor(placed.item, this.anchors);
      const e = item === placed.item ? placed : { item, seq: placed.seq };
      change.after[i] = e;
      this.replicaStamp.set(e.item.id, Math.max(this.replicaStamp.get(e.item.id) ?? 0, e.item.updatedAt));
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
