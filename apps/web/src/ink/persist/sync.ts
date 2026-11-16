import { type InkDiff, type InkItem, InkItemSchema, mergeInkItems, recordInkDiff } from "@naadi/shared";
import type { PendingStore } from "./db";

/**
 * InkSync: one surface's ink replica on this device, framework-free (useInkLayer wires it to React, the engine
 * and the network).
 *
 * - `replica`: the merged layer as this device knows it, tombstones included (every item the engine shows is in it).
 * - `pending`: the latest unsent version per item id (recordInkDiff: deletions become tombstones stamped now).
 *   Every change goes to the PendingStore (IndexedDB) before any network call, and leaves it only when the server
 *   confirmed that exact version, so nothing is lost on reload, pagehide or offline, and resending is harmless
 *   (the server's merge is idempotent).
 * - Server results are merged with mergeInkItems' order; a pending local version that is newer stays (it will be
 *   sent), an older one is dropped (the server's copy won). Remote changes reach the engine through `onRemote`
 *   (InkEngine.applyRemote: no undo steps).
 *
 * Network operations run one at a time (a refresh never races a send over the sync cursor).
 */

export type InkSaveState = "loading" | "saved" | "saving" | "local" | "error";
export type InkFailure = "offline" | "server" | "limit";

export interface LayerPayload {
  rev: number;
  items: InkItem[];
  reset: boolean;
  outdated: number;
}

export interface ChangesPayload {
  rev: number;
  applied: string[];
  rejected: { id: string | null; reason: "invalid" | "stale" | "duplicate" }[];
  remote: InkItem[];
  reset: boolean;
}

export interface InkTransport {
  /** GET: a full load (`since` null) or the changes after `since`. */
  load(since: number | null): Promise<LayerPayload>;
  /** POST changes. `keepalive` lets the request outlive a hidden page (body must stay under 64 KB). */
  send(baseRev: number, items: InkItem[], opts: { keepalive: boolean }): Promise<ChangesPayload>;
  /** Start a keepalive POST synchronously during `pagehide`; false when it could not be started. */
  beacon(baseRev: number, items: InkItem[]): boolean;
}

export interface InkSyncOptions {
  transport: InkTransport;
  store: PendingStore;
  /** Remote versions the engine must merge (InkEngine.applyRemote). */
  onRemote: (items: InkItem[]) => void;
  onChange?: () => void;
  /** Classifies a send/load error: no connection (ink is safe on the device) vs refused by the server. */
  classify: (err: unknown) => InkFailure;
  now?: () => number;
  /** Flush after this much quiet time... */
  idleMs?: number;
  /** ...or as soon as this many items are pending. */
  batchItems?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

/** Per request: the API's limits are 500 items and 2 MB; keepalive bodies must stay under 64 KB in total. */
export const MAX_SEND_ITEMS = 500;
export const MAX_SEND_BYTES = 1_500_000;
export const KEEPALIVE_BYTES = 60_000;

/** Does `a` win over `b` in mergeInkItems' order (full tie: no)? */
export function beats(a: InkItem, b: InkItem): boolean {
  if (a.updatedAt !== b.updatedAt) return a.updatedAt > b.updatedAt;
  return Boolean(a.deleted) && !b.deleted;
}

/** Items that fit one request: at most `maxItems` and `maxBytes` of JSON (always at least one item). */
export function chunk(items: readonly InkItem[], maxBytes: number, maxItems = MAX_SEND_ITEMS): InkItem[] {
  const out: InkItem[] = [];
  let bytes = 32;
  for (const item of items) {
    const size = JSON.stringify(item).length + 1;
    if (out.length > 0 && (bytes + size > maxBytes || out.length >= maxItems)) break;
    out.push(item);
    bytes += size;
  }
  return out;
}

export class InkSync {
  private readonly o: Required<Omit<InkSyncOptions, "onChange">> & Pick<InkSyncOptions, "onChange">;
  private readonly replica = new Map<string, InkItem>();
  private readonly pending = new Map<string, InkItem>();
  private rev = 0;
  private restored = false;
  private hadSnapshot = false;
  private busy = false;
  private queue: Promise<void> = Promise.resolve();
  private persisted: Promise<void> = Promise.resolve();
  private timer: unknown = null;
  private retryTimer: unknown = null;
  private retryMs = 0;
  private failure: InkFailure | null = null;
  /** IndexedDB accepted our writes (false in private mode or when storage is full). */
  private durable = true;
  private stopped = false;
  outdated = 0;

  constructor(options: InkSyncOptions) {
    this.o = {
      now: Date.now,
      idleMs: 800,
      batchItems: 50,
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
      ...options,
    };
  }

  // ── State ──

  get state(): InkSaveState {
    if (!this.restored) return "loading";
    if (this.pending.size === 0 && !this.busy) return "saved";
    if (this.failure === "server" || this.failure === "limit") return "error";
    if (this.failure === "offline") return this.durable ? "local" : "error";
    return "saving";
  }

  get lastFailure(): InkFailure | null {
    return this.failure;
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  get cursor(): number {
    return this.rev;
  }

  /** Live items of the replica (what the engine should show), by id. */
  liveItems(): InkItem[] {
    return mergeInkItems([...this.replica.values()], []).filter((i) => !i.deleted);
  }

  /** Every item of the replica, tombstones included (tests, convergence checks). */
  replicaItems(): InkItem[] {
    return mergeInkItems([...this.replica.values()], []);
  }

  private changed(): void {
    this.o.onChange?.();
  }

  // ── Mount ──

  /**
   * Read this surface's snapshot and unsent ink from the device. Returns the items to show at once (before the
   * network answers). Never throws: without IndexedDB the layer starts empty and the server fills it.
   */
  async restore(): Promise<InkItem[]> {
    let snapshot: { rev: number; items: InkItem[] } | null = null;
    let pending: InkItem[] = [];
    try {
      [snapshot, pending] = await Promise.all([this.o.store.readLayer(), this.o.store.readPending()]);
    } catch {
      this.durable = false;
    }
    const valid = (items: readonly InkItem[]) => items.filter((i) => InkItemSchema.safeParse(i).success);
    const snap = valid(snapshot?.items ?? []);
    const mine = valid(pending);
    this.hadSnapshot = snapshot !== null;
    this.rev = snapshot?.rev ?? 0;
    for (const item of mergeInkItems(snap, mine)) this.replica.set(item.id, item);
    // A pending version that the snapshot already beats is obsolete (the server had a newer copy).
    for (const item of mine) {
      const r = this.replica.get(item.id);
      if (r === item || (r && !beats(r, item))) this.pending.set(item.id, item);
    }
    this.restored = true;
    this.changed();
    return this.liveItems();
  }

  // ── Local edits ──

  /** One committed engine action (onStrokesChange). */
  record(diff: InkDiff): void {
    if (this.stopped) return;
    const ids = new Set([...diff.added.map((i) => i.id), ...diff.updated.map((i) => i.id), ...diff.removed]);
    const base = [...ids].map((id) => this.replica.get(id)).filter((i): i is InkItem => i !== undefined);
    const out = recordInkDiff(base, diff, this.o.now());
    const known = new Set(out.map((i) => i.id));
    // An update to an item this replica never saw (should not happen) is still kept, as an upsert.
    for (const item of diff.updated) if (!known.has(item.id)) out.push(item);
    for (const item of out) {
      this.replica.set(item.id, item);
      this.pending.set(item.id, item);
    }
    this.persist(out);
    this.failure = this.failure === "offline" ? "offline" : null;
    this.changed();
    if (this.pending.size >= this.o.batchItems) this.schedule(0);
    else this.schedule(this.o.idleMs);
  }

  private persist(items: readonly InkItem[]): void {
    const write = this.o.store.putPending(items).then(
      () => {
        this.durable = true;
      },
      () => {
        this.durable = false;
      },
    );
    this.persisted = this.persisted.then(() => write);
  }

  // ── Network ──

  private schedule(ms: number): void {
    if (this.stopped) return;
    if (this.timer !== null) this.o.clearTimer(this.timer);
    this.timer = this.o.setTimer(() => {
      this.timer = null;
      void this.flush();
    }, ms);
  }

  private serial(fn: () => Promise<void>): Promise<void> {
    const run = this.queue.then(async () => {
      if (this.stopped) return;
      this.busy = true;
      this.changed();
      try {
        await fn();
      } finally {
        this.busy = false;
        this.changed();
      }
    });
    this.queue = run.catch(() => undefined);
    return run;
  }

  /** Send everything pending (several requests if needed). Resolves when done or failed (state says which). */
  flush(opts: { keepalive?: boolean } = {}): Promise<void> {
    if (this.timer !== null) {
      this.o.clearTimer(this.timer);
      this.timer = null;
    }
    return this.serial(async () => {
      await this.persisted; // on the device first
      while (this.pending.size > 0 && !this.stopped) {
        const batch = chunk([...this.pending.values()], opts.keepalive ? KEEPALIVE_BYTES : MAX_SEND_BYTES);
        let out: ChangesPayload;
        try {
          out = await this.o.transport.send(this.rev, batch, { keepalive: Boolean(opts.keepalive) });
        } catch (err) {
          this.fail(this.o.classify(err));
          return;
        }
        this.ok();
        await this.confirm(batch, out);
        if (opts.keepalive) break; // one small request while the page is going away
      }
    });
  }

  /** Fetch other devices' changes (focus, visible, every 30 s, reconnect). Then send what is pending. */
  refresh(): Promise<void> {
    const run = this.serial(async () => {
      let out: LayerPayload;
      const since = this.hadSnapshot || this.rev > 0 ? this.rev : null;
      try {
        out = await this.o.transport.load(since);
      } catch (err) {
        if (this.pending.size > 0) this.fail(this.o.classify(err));
        throw err;
      }
      this.hadSnapshot = true;
      this.outdated = out.outdated;
      this.applyServer(out.items, out.reset || since === null);
      this.rev = out.rev;
      this.snapshot();
      if (this.failure === "offline") this.ok();
    });
    return run.then(() => (this.pending.size > 0 ? this.flush() : undefined));
  }

  /** `pagehide`: start one keepalive request synchronously with what fits; the rest stays on the device. */
  beacon(): void {
    if (this.pending.size === 0) return;
    const batch = chunk([...this.pending.values()], KEEPALIVE_BYTES);
    this.o.transport.beacon(this.rev, batch);
  }

  /** Retry now (the "Retry" button, the `online` event). */
  retry(): Promise<void> {
    this.retryMs = 0;
    if (this.retryTimer !== null) this.o.clearTimer(this.retryTimer);
    this.retryTimer = null;
    return this.flush();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) this.o.clearTimer(this.timer);
    if (this.retryTimer !== null) this.o.clearTimer(this.retryTimer);
    this.timer = null;
    this.retryTimer = null;
  }

  private ok(): void {
    this.failure = null;
    this.retryMs = 0;
  }

  private fail(kind: InkFailure): void {
    this.failure = kind;
    this.changed();
    if (kind === "limit") return; // needs the student to erase something; retried on the next edit or Retry
    this.retryMs = Math.min(this.retryMs ? this.retryMs * 2 : kind === "offline" ? 2_000 : 5_000, 60_000);
    if (this.retryTimer !== null) this.o.clearTimer(this.retryTimer);
    this.retryTimer = this.o.setTimer(() => {
      this.retryTimer = null;
      void this.flush();
    }, this.retryMs);
  }

  private async confirm(batch: InkItem[], out: ChangesPayload): Promise<void> {
    const applied = new Set(out.applied);
    const settled = new Map<string, string>(); // id -> reason it leaves pending
    for (const id of applied) settled.set(id, "applied");
    for (const r of out.rejected) if (r.id && !settled.has(r.id)) settled.set(r.id, r.reason);
    const done: InkItem[] = [];
    for (const item of batch) {
      const reason = settled.get(item.id);
      if (!reason) continue;
      if (reason === "invalid") console.warn("naadi: the server refused an ink item as invalid; it was dropped");
      const cur = this.pending.get(item.id);
      if (cur === item) this.pending.delete(item.id); // a newer local edit stays pending
      done.push(item);
    }
    // An item the server neither applied nor rejected (should not happen) stays pending and is resent.
    try {
      await this.o.store.ackPending(done);
    } catch {
      this.durable = false;
    }
    this.applyServer(out.remote, out.reset);
    this.rev = Math.max(this.rev, out.rev);
    this.snapshot();
  }

  /** Merge server versions into the replica and hand the engine what it must change. */
  private applyServer(items: readonly InkItem[], full: boolean): void {
    const toEngine: InkItem[] = [];
    const drop: InkItem[] = [];
    const valid = items.filter((i) => InkItemSchema.safeParse(i).success);
    if (full) {
      // A full load lists every live item: anything else this device shows (and has not just drawn) is gone.
      const live = new Set(valid.map((i) => i.id));
      for (const [id, item] of this.replica) {
        if (live.has(id) || this.pending.has(id)) continue;
        if (!item.deleted) toEngine.push({ ...item, deleted: true }); // same stamp: a tombstone wins the tie
        this.replica.delete(id);
      }
    }
    for (const item of valid) {
      const mine = this.pending.get(item.id);
      if (mine) {
        if (beats(mine, item)) continue; // ours is newer: it stays pending and will be sent
        this.pending.delete(item.id); // the server's copy won (or is the same): adopt it
        drop.push(mine);
      }
      const known = this.replica.get(item.id);
      if (known && !beats(item, known) && !mine) continue;
      this.replica.set(item.id, item);
      toEngine.push(item);
    }
    if (drop.length) void this.o.store.ackPending(drop).catch(() => undefined);
    if (toEngine.length) this.o.onRemote(toEngine);
  }

  private snapshot(): void {
    void this.o.store.writeLayer(this.rev, this.replicaItems()).catch(() => undefined);
  }
}
