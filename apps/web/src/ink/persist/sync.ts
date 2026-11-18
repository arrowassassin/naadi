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
 * - The server clamps a stamp from a clock running ahead and sends its stored copy back; this device adopts it
 *   (`onRemote(items, { authoritative: true })`), so its future stamp never outlives the request.
 * - An item the server refuses as `invalid` moves to a quarantine on the device (never dropped silently): the
 *   state is "error" with failure "rejected" until the student retries (resend) or discards it.
 *
 * Network operations run one at a time (a refresh never races a send over the sync cursor).
 */

export type InkSaveState = "loading" | "saved" | "saving" | "local" | "error";
export type InkFailure = "offline" | "server" | "limit" | "rejected";

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
  /** Cancel in-flight loads/sends (the page is going away; what is pending stays on the device). */
  abort?(): void;
}

export interface InkSyncOptions {
  transport: InkTransport;
  store: PendingStore;
  /** Remote versions the engine must merge (InkEngine.applyRemote); `authoritative`: the server's copy of this
   * device's own write, adopted whatever its stamp. */
  onRemote: (items: InkItem[], opts?: { authoritative?: boolean }) => void;
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
  private readonly rejected = new Map<string, InkItem>();
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
  private loadedOnce = false;
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
    if (this.rejected.size > 0 && !this.busy) return "error";
    if (this.pending.size === 0 && !this.busy) return "saved";
    if (this.failure === "server" || this.failure === "limit") return "error";
    if (this.failure === "offline") return this.durable ? "local" : "error";
    return "saving";
  }

  /** The server has answered at least once and nothing is in flight or waiting to be sent (tests, navigation). */
  get idle(): boolean {
    return this.loadedOnce && !this.busy && this.pending.size === 0;
  }

  get lastFailure(): InkFailure | null {
    if (this.rejected.size > 0 && this.failure === null) return "rejected";
    return this.failure;
  }

  /** Items the server refused, kept on the device until retried or discarded. */
  get rejectedCount(): number {
    return this.rejected.size;
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
    let rejected: InkItem[] = [];
    try {
      [snapshot, pending, rejected] = await Promise.all([
        this.o.store.readLayer(),
        this.o.store.readPending(),
        this.o.store.readRejected(),
      ]);
    } catch {
      this.durable = false;
    }
    const valid = (items: readonly InkItem[]) => items.filter((i) => InkItemSchema.safeParse(i).success);
    const snap = valid(snapshot?.items ?? []);
    const mine = valid(pending);
    for (const item of rejected) this.rejected.set(item.id, item);
    this.hadSnapshot = snapshot !== null;
    this.rev = snapshot?.rev ?? 0;
    // Quarantined items are still shown (the student decides): they join the replica like pending ones.
    for (const item of mergeInkItems(mergeInkItems(snap, mine), [...this.rejected.values()]))
      this.replica.set(item.id, item);
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
      this.loadedOnce = true;
      this.outdated = out.outdated;
      this.applyServer(out.items, out.reset || since === null);
      this.rev = out.rev;
      this.snapshot();
      if (this.failure === "offline") this.ok();
    });
    return run.then(() => (this.pending.size > 0 ? this.flush() : undefined));
  }

  /**
   * `pagehide`: start one keepalive request synchronously with what fits (the rest stays on the device), then
   * cancel our ordinary in-flight requests ourselves rather than leave them to be torn down by the unload.
   */
  beacon(): void {
    if (this.pending.size > 0) {
      const batch = chunk([...this.pending.values()], KEEPALIVE_BYTES);
      this.o.transport.beacon(this.rev, batch);
    }
    this.o.transport.abort?.();
  }

  /** Retry now (the "Retry" button, the `online` event). Only the button (`rejected: true`) resends quarantined
   * items: the server refused them once, so only the student's choice sends them again. */
  retry(opts: { rejected?: boolean } = {}): Promise<void> {
    if (opts.rejected && this.rejected.size > 0) {
      const again = [...this.rejected.values()].filter((i) => {
        const cur = this.pending.get(i.id);
        return !cur || !beats(cur, i);
      });
      const ids = [...this.rejected.keys()];
      this.rejected.clear();
      for (const item of again) this.pending.set(item.id, item);
      this.persist(again);
      this.persisted = this.persisted.then(() => this.o.store.releaseRejected(ids).catch(() => undefined));
      this.changed();
    }
    this.retryMs = 0;
    if (this.retryTimer !== null) this.o.clearTimer(this.retryTimer);
    this.retryTimer = null;
    return this.flush();
  }

  /** Drop the quarantined items from this device and from the canvas (the "Discard" button). */
  discardRejected(): void {
    if (this.rejected.size === 0) return;
    const gone: InkItem[] = [];
    for (const [id, item] of this.rejected) {
      const cur = this.replica.get(id);
      // Only what is still the quarantined version: a newer edit of the same stroke stays.
      if (cur && !beats(cur, item)) {
        this.replica.delete(id);
        if (!item.deleted) gone.push({ ...item, deleted: true }); // same stamp: a tombstone wins the tie locally
      }
    }
    const ids = [...this.rejected.keys()];
    this.rejected.clear();
    void this.o.store.releaseRejected(ids).catch(() => undefined);
    if (gone.length) this.o.onRemote(gone);
    this.snapshot();
    this.changed();
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
    const remoteIds = new Set(out.remote.map((item) => item.id));
    const done: InkItem[] = [];
    const refused: InkItem[] = [];
    for (const item of batch) {
      const reason = settled.get(item.id);
      if (!reason) continue;
      const cur = this.pending.get(item.id);
      if (cur === item) this.pending.delete(item.id); // a newer local edit stays pending
      // Below the purge horizon the server cannot distinguish a long-offline drawing from an old edit of
      // deleted ink. Keep a local copy for the student without attempting to resurrect it on the server.
      const missingAfterReset =
        reason === "stale" && out.reset && cur === item && !item.deleted && !remoteIds.has(item.id);
      if (reason === "invalid" || missingAfterReset) {
        refused.push(item);
        this.rejected.set(item.id, item);
      } else done.push(item);
    }
    // An item the server neither applied nor rejected (should not happen) stays pending and is resent.
    try {
      await Promise.all([this.o.store.ackPending(done), this.o.store.quarantine(refused)]);
    } catch {
      this.durable = false;
    }
    // The server's copy of something this request sent is the merge result for that id, whatever the stamps say
    // here: applied with a clamped stamp, or stale (the stored row won, including a full tie, and a clamped stamp
    // that lost). Adopt it, unless the student has edited the item again since (that version is pending).
    const sent = new Map(batch.map((i) => [i.id, i]));
    const adopt: InkItem[] = [];
    const rest: InkItem[] = [];
    for (const item of out.remote) {
      const mine = sent.get(item.id);
      const why = settled.get(item.id);
      if (mine && (why === "applied" || why === "stale") && InkItemSchema.safeParse(item).success) {
        const cur = this.pending.get(item.id);
        if (!cur || cur === mine) {
          this.replica.set(item.id, item);
          adopt.push(item);
        }
        continue;
      }
      rest.push(item);
    }
    if (adopt.length) this.o.onRemote(adopt, { authoritative: true });
    this.applyServer(rest, out.reset, adopt);
    this.rev = Math.max(this.rev, out.rev);
    this.snapshot();
  }

  /** Merge server versions into the replica and hand the engine what it must change. */
  private applyServer(items: readonly InkItem[], full: boolean, alsoLive: readonly InkItem[] = []): void {
    const toEngine: InkItem[] = [];
    const drop: InkItem[] = [];
    const valid = items.filter((i) => InkItemSchema.safeParse(i).success);
    if (full) {
      // A full load lists every live item: anything else this device shows (and has not just drawn) is gone.
      const live = new Set([...valid, ...alsoLive].map((i) => i.id));
      for (const [id, item] of this.replica) {
        if (live.has(id) || this.pending.has(id) || this.rejected.has(id)) continue;
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
