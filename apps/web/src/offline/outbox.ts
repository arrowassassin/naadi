import { uuidv7 } from "@naadi/shared";
import { type ConflictRow, naadiDb, type OutboxEntity, type OutboxRow, type OutboxState, track } from "./db";

/**
 * The outbox (segment 18): every write the student makes goes here first, online or not, and is replayed until
 * the server answered it. One table (`naadi-v1.outbox`), one scheduler, one status line.
 *
 * - Rows are kept in memory (synchronous reads for the screens) and in IndexedDB (written before any request, so a
 *   reload, a closed tab or no connection loses nothing). Without IndexedDB (a private window) rows live for the
 *   page's life.
 * - Coalescing per (entity, key) for last-writer-wins entities: a new op replaces the queued one. A row that was
 *   already sent at least once gets a NEW op id when it changes, because the server caches the answer to an
 *   `Idempotency-Key` for 24 h: reusing it would replay the old answer and silently drop the newer edit.
 *   A row whose request is on the wire is never changed; the newer op queues behind it.
 * - Replay: on `online`, on `visibilitychange` to visible, at start and every 30 s while rows wait. FIFO per entity
 *   (seq order), entities in parallel; an entity can wait for others (`after`: a note filed in a notebook made
 *   offline waits for that notebook). Exponential backoff with jitter, 2 s to 5 min.
 * - Outcomes: `ok` clears the row (unless it changed while in flight); `conflict` (the server's version won the
 *   LWW race) clears it, keeps the losing local version in `conflicts` for 7 days and marks the row "Changed on
 *   another device" for this session; `unreachable` / `retry` keep it and stop that entity's lane; `failed` parks
 *   it (4xx other than 408/409/425/429) with Retry / Discard in the sync sheet. Nothing is ever dropped silently.
 */

export type SendOutcome =
  | { kind: "ok"; server?: unknown }
  | { kind: "conflict"; server: unknown }
  | { kind: "unreachable" }
  | { kind: "retry"; code: string }
  | { kind: "failed"; code: string };

export interface EntityHandler {
  entity: OutboxEntity;
  /** `replace`: a new op for the same key replaces the queued one (LWW). `append`: every op is kept (attempts,
   * card reviews). A function: entity-specific folding (returns the body to keep, or null to drop the queued op
   * and the new one, e.g. a create and a delete of a note never sent). */
  coalesce: "replace" | "append" | ((queued: OutboxRow, next: Draft) => unknown | null);
  /** Entities whose due rows must be sent first. */
  after?: readonly OutboxEntity[];
  send?(row: OutboxRow): Promise<SendOutcome>;
  /** Batched entities (ink: one request per surface). Returns an outcome per op id; a missing one is `retry`. */
  sendGroup?(group: string, rows: OutboxRow[]): Promise<Map<string, SendOutcome>>;
  /**
   * Delegated entities (notes, notebooks, ink) keep their own, already-proven send rules and write their rows with
   * `putEntityRow` / `removeEntityRow`; the scheduler only calls `flush` (instead of `send`) during a pass, so they
   * still share one table, one status line, the triggers and the sync sheet.
   */
  flush?(user: string): Promise<void>;
  /** After an answer (adopt the stored row into the cache, refresh queries). Never throws into the scheduler. */
  settled?(row: OutboxRow, outcome: SendOutcome): void;
}

export interface Draft {
  entity: OutboxEntity;
  key: string;
  group?: string;
  method: OutboxRow["method"];
  body: unknown;
  /** Defaults to the server clock now. */
  client_updated_at_ms?: number;
}

const handlers = new Map<OutboxEntity, EntityHandler>();

export function registerEntity(h: EntityHandler): void {
  handlers.set(h.entity, h);
}

export function handlerFor(entity: OutboxEntity): EntityHandler | undefined {
  return handlers.get(entity);
}

// ── Clock (the server's, as ink measured it) ──

let clock: () => number = () => Date.now();
/** Use the server-adjusted clock for LWW stamps (ink/persist/clock.ts's serverNow). */
export function setStampClock(fn: () => number): void {
  clock = fn;
}
export function stampNow(): number {
  return Math.round(clock());
}

let seqClock = 0;
/** A FIFO position after every row this device has queued. */
export function nextSeq(): number {
  seqClock = Math.max(seqClock + 1, Date.now() * 1000);
  return seqClock;
}

// ── Memory mirror + persistence ──

const rows = new Map<string, OutboxRow>();
const loaded = new Set<string>();
const listeners = new Set<() => void>();
let version = 0;
/** Rows whose request is on the wire (op ids). */
const inflight = new Set<string>();
/** `${entity}\u0000${key}` changed on another device during this session. */
const changedElsewhere = new Set<string>();
let syncing = 0;

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeOutbox(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function outboxVersion(): number {
  return version;
}

export { whenPersisted } from "./db";

function persist(row: OutboxRow): Promise<void> {
  return track(
    (async () => {
      try {
        await naadiDb().outbox.put(row);
      } catch {
        // IndexedDB unavailable: the row lives in memory for this page
      }
    })(),
  );
}

function unpersist(opId: string): Promise<void> {
  return track(
    (async () => {
      try {
        await naadiDb().outbox.delete(opId);
      } catch {
        // nothing stored
      }
    })(),
  );
}

async function put(row: OutboxRow): Promise<void> {
  rows.set(row.op_id, row);
  emit();
  await persist(row);
}

async function remove(opId: string): Promise<void> {
  rows.delete(opId);
  emit();
  await unpersist(opId);
}

/** True when rows survive a reload (IndexedDB opens). One-time migrations only delete their old store when it is. */
export async function outboxPersists(): Promise<boolean> {
  try {
    await naadiDb().outbox.count();
    return true;
  } catch {
    return false;
  }
}

/** Read this user's rows from IndexedDB (once per page and user) and purge conflicts older than 7 days. */
export async function loadOutbox(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  try {
    const stored = await naadiDb().outbox.where("user_id").equals(user).toArray();
    for (const r of stored) {
      if (rows.has(r.op_id)) continue;
      // A row "sending" when the page went away may or may not have reached the server: send it again (the
      // Idempotency-Key makes that safe).
      rows.set(r.op_id, r.state === "sending" ? { ...r, state: "queued" } : r);
      seqClock = Math.max(seqClock, r.seq);
    }
    await naadiDb()
      .conflicts.where("at")
      .below(Date.now() - CONFLICT_KEEP_MS)
      .delete();
  } catch {
    // no IndexedDB: memory only
  }
  emit();
}

export const CONFLICT_KEEP_MS = 7 * 24 * 60 * 60 * 1000;

/** The rows of one user, FIFO (seq) order. */
export function outboxRows(user: string, entity?: OutboxEntity): OutboxRow[] {
  return [...rows.values()]
    .filter((r) => r.user_id === user && (entity === undefined || r.entity === entity))
    .sort((a, b) => a.seq - b.seq);
}

export function rowsFor(user: string, entity: OutboxEntity, key: string): OutboxRow[] {
  return outboxRows(user, entity).filter((r) => r.key === key);
}

/** Queue one write. Returns the row that now carries it. */
export async function enqueue(user: string, draft: Draft): Promise<OutboxRow | null> {
  const h = handlers.get(draft.entity);
  const stamp = draft.client_updated_at_ms ?? stampNow();
  const idle = rowsFor(user, draft.entity, draft.key).filter((r) => !inflight.has(r.op_id) && r.state !== "failed");
  const last = idle.at(-1);
  const strategy = h?.coalesce ?? "append";
  if (last && strategy !== "append") {
    let body: unknown = draft.body;
    if (typeof strategy === "function") {
      body = strategy(last, { ...draft, client_updated_at_ms: stamp });
      if (body === null) {
        await remove(last.op_id);
        return null;
      }
    }
    const next: OutboxRow = {
      ...last,
      // Sent before: its answer may be cached server-side under the old key (see the module comment).
      op_id: last.attempts > 0 ? uuidv7() : last.op_id,
      method: draft.method,
      body,
      client_updated_at_ms: stamp,
      rev: last.rev + 1,
      attempts: 0,
      next_at: 0,
      last_error: null,
    };
    if (next.op_id !== last.op_id) await remove(last.op_id);
    await put(next);
    schedule(0);
    return next;
  }
  const row: OutboxRow = {
    op_id: uuidv7(),
    user_id: user,
    entity: draft.entity,
    key: draft.key,
    group: draft.group ?? "",
    method: draft.method,
    body: draft.body,
    client_updated_at_ms: stamp,
    queued_at: Date.now(),
    seq: nextSeq(),
    rev: 1,
    attempts: 0,
    next_at: 0,
    state: "queued",
    last_error: null,
  };
  await put(row);
  schedule(0);
  return row;
}

const waiters = new Map<string, (o: SendOutcome) => void>();

/**
 * Queue one write and try it now: every write goes through the outbox, online or not. Resolves with the server's
 * answer when it came during this replay (`ok` / `conflict` / `failed`), or null when the op stays queued (offline,
 * backing off, or behind an older op of the same entity that must go first).
 */
export async function submit(
  user: string,
  draft: Draft,
): Promise<{ row: OutboxRow | null; outcome: SendOutcome | null }> {
  const row = await enqueue(user, draft);
  if (!row) return { row, outcome: null };
  let outcome: SendOutcome | null = null;
  waiters.set(row.op_id, (o) => {
    outcome = o;
  });
  try {
    await replay();
    // The pass that was running when we queued may have finished before reaching this row; one more.
    const still = rows.get(row.op_id);
    if (outcome === null && still?.state === "queued" && still.next_at <= Date.now()) await replay();
  } finally {
    waiters.delete(row.op_id);
  }
  return { row, outcome };
}

/**
 * A delegated entity's row for (entity, key): created or replaced. The op id (the `Idempotency-Key`) changes
 * whenever the body changes, so a replay of an earlier, cached answer can never stand in for a newer change.
 */
export async function putEntityRow(
  user: string,
  entity: OutboxEntity,
  key: string,
  body: unknown,
  opts: EntityRowOpts = {},
): Promise<OutboxRow> {
  const [row] = await putEntityRows(user, entity, [{ key, body, ...opts }]);
  return row as OutboxRow;
}

export interface EntityRowOpts {
  state?: OutboxState;
  last_error?: string | null;
  group?: string;
  method?: OutboxRow["method"];
  /** FIFO position (an entity that numbers its own ops, e.g. notebooks). */
  seq?: number;
}

/** Many rows of one delegated entity in one IndexedDB transaction (ink records 50 strokes at once). */
export async function putEntityRows(
  user: string,
  entity: OutboxEntity,
  list: readonly ({ key: string; body: unknown } & EntityRowOpts)[],
): Promise<OutboxRow[]> {
  const out: OutboxRow[] = [];
  const dropped: string[] = [];
  for (const item of list) {
    const prev = rowsFor(user, entity, item.key)[0];
    const sameBody = prev !== undefined && JSON.stringify(prev.body) === JSON.stringify(item.body);
    const row: OutboxRow = {
      op_id: prev && sameBody ? prev.op_id : uuidv7(),
      user_id: user,
      entity,
      key: item.key,
      group: item.group ?? prev?.group ?? "",
      method: item.method ?? prev?.method ?? "POST",
      body: item.body,
      client_updated_at_ms: prev && sameBody ? prev.client_updated_at_ms : stampNow(),
      queued_at: prev?.queued_at ?? Date.now(),
      seq: item.seq ?? prev?.seq ?? nextSeq(),
      rev: (prev?.rev ?? 0) + (sameBody ? 0 : 1),
      attempts: prev && sameBody ? prev.attempts : 0,
      next_at: 0,
      state: item.state ?? "queued",
      last_error: item.last_error ?? null,
    };
    if (prev && prev.op_id !== row.op_id) {
      rows.delete(prev.op_id);
      dropped.push(prev.op_id);
    }
    rows.set(row.op_id, row);
    out.push(row);
  }
  emit();
  await track(
    (async () => {
      try {
        const d = naadiDb();
        await d.transaction("rw", d.outbox, async () => {
          if (dropped.length) await d.outbox.bulkDelete(dropped);
          await d.outbox.bulkPut(out);
        });
      } catch {
        // IndexedDB unavailable: the rows live in memory for this page
      }
    })(),
  );
  if (out.some((r) => r.state === "queued")) schedule(0);
  return out;
}

export async function removeEntityRow(user: string, entity: OutboxEntity, key: string): Promise<void> {
  for (const r of rowsFor(user, entity, key)) await remove(r.op_id);
}

/** Write many rows in one go (one-time migrations). */
export async function importRows(list: OutboxRow[]): Promise<void> {
  for (const r of list) {
    rows.set(r.op_id, r);
    seqClock = Math.max(seqClock, r.seq);
  }
  emit();
  try {
    await naadiDb().outbox.bulkPut(list);
  } catch {
    // memory only
  }
}

/** Remove rows directly (an entity adapter that confirmed them some other way, or the student discarded them). */
export async function dropRows(opIds: readonly string[]): Promise<void> {
  for (const id of opIds) await remove(id);
}

/** Put a parked row back in the queue (sync sheet: Retry). */
export async function retryRow(opId: string): Promise<void> {
  const r = rows.get(opId);
  if (!r) return;
  await put({ ...r, state: "queued", attempts: 0, next_at: 0, last_error: null });
  schedule(0);
}

/** Retry every parked row of a user. */
export async function retryAll(user: string): Promise<void> {
  for (const r of outboxRows(user)) if (r.state === "failed") await retryRow(r.op_id);
}

/** Forget a parked row (sync sheet: Discard). Its local version is kept in `conflicts` so it can still be read. */
export async function discardRow(opId: string): Promise<void> {
  const r = rows.get(opId);
  if (!r) return;
  await keepConflict(r, null);
  await remove(opId);
}

async function keepConflict(r: OutboxRow, server: unknown): Promise<void> {
  const c: ConflictRow = { user_id: r.user_id, entity: r.entity, key: r.key, local: r.body, server, at: Date.now() };
  try {
    await naadiDb().conflicts.add(c);
  } catch {
    // no IndexedDB: the notice still shows for this session
  }
}

/** A delegated entity's write lost to a newer version on the server: keep the local copy and mark the row. */
export async function recordConflict(
  user: string,
  entity: OutboxEntity,
  key: string,
  local: unknown,
  server: unknown,
): Promise<void> {
  changedElsewhere.add(`${entity}\u0000${key}`);
  await keepConflict({ user_id: user, entity, key, body: local } as OutboxRow, server);
  emit();
}

export async function conflictsFor(user: string): Promise<ConflictRow[]> {
  try {
    const all = await naadiDb().conflicts.where("user_id").equals(user).toArray();
    return all.filter((c) => c.at >= Date.now() - CONFLICT_KEEP_MS).sort((a, b) => b.at - a.at);
  } catch {
    return [];
  }
}

export function wasChangedElsewhere(entity: OutboxEntity, key: string): boolean {
  return changedElsewhere.has(`${entity}\u0000${key}`);
}

// ── Status ──

export interface OutboxStatus {
  pending: number;
  failed: number;
  syncing: boolean;
}

export function outboxStatus(user: string | null): OutboxStatus {
  if (!user) return { pending: 0, failed: 0, syncing: false };
  let pending = 0;
  let failed = 0;
  for (const r of rows.values()) {
    if (r.user_id !== user) continue;
    if (r.state === "failed") failed += 1;
    else pending += 1;
  }
  return { pending, failed, syncing: syncing > 0 };
}

// ── Scheduler ──

export const BACKOFF_MIN_MS = 2_000;
export const BACKOFF_MAX_MS = 5 * 60_000;
export const POLL_MS = 30_000;

export function backoffMs(attempts: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.max(0, attempts - 1));
  // ±20 % jitter, so devices coming back online together don't retry in lockstep.
  return Math.round(base * (0.8 + 0.4 * random()));
}

let activeUser: string | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let running: Promise<void> | null = null;
let rerun = false;
let isOnline: () => boolean = () => (typeof navigator === "undefined" ? true : navigator.onLine !== false);

/** Test hook. */
export function setOnlineProbe(fn: () => boolean): void {
  isOnline = fn;
}

function schedule(delay: number): void {
  if (!activeUser) return;
  if (timer) clearTimeout(timer);
  timer = setTimeout(
    () => {
      timer = null;
      void replay();
    },
    Math.max(0, delay),
  );
}

/** Start replaying this user's outbox (app start, after sign-in). */
export async function startOutbox(user: string): Promise<void> {
  activeUser = user;
  await loadOutbox(user);
  schedule(0);
}

/** Stop replaying (sign-out). Rows stay stored for that user. */
export function stopOutbox(): void {
  activeUser = null;
  if (timer) clearTimeout(timer);
  timer = null;
}

export function activeOutboxUser(): string | null {
  return activeUser;
}

/** Replay now. Concurrent calls join the running pass, and one more pass follows it. */
export function replay(): Promise<void> {
  if (running) {
    rerun = true;
    return running;
  }
  running = (async () => {
    try {
      do {
        rerun = false;
        await pass();
      } while (rerun);
    } finally {
      running = null;
      const user = activeUser;
      if (user) {
        const waiting = outboxRows(user).filter((r) => r.state === "queued");
        if (waiting.length) {
          const soonest = Math.min(...waiting.map((r) => r.next_at));
          schedule(Math.min(POLL_MS, Math.max(0, soonest - Date.now())));
        }
      }
    }
  })();
  return running;
}

async function pass(): Promise<void> {
  const user = activeUser;
  if (!user || !isOnline()) return;
  const entities = [...new Set(outboxRows(user).map((r) => r.entity))];
  if (!entities.length) return;
  syncing += 1;
  emit();
  try {
    const done = new Map<OutboxEntity, Promise<void>>();
    const run = (e: OutboxEntity): Promise<void> => {
      const known = done.get(e);
      if (known) return known;
      const h = handlers.get(e);
      const deps = (h?.after ?? []).filter((d) => d !== e && entities.includes(d));
      const p = Promise.all(deps.map(run)).then(() => {
        // A dependency still has rows due: wait for the next pass rather than send out of order.
        const blocked = deps.some((d) => outboxRows(user, d).some((r) => r.state === "queued"));
        return blocked ? undefined : lane(user, e);
      });
      done.set(e, p);
      return p;
    };
    await Promise.all(entities.map(run));
  } finally {
    syncing -= 1;
    emit();
  }
}

async function lane(user: string, entity: OutboxEntity): Promise<void> {
  const h = handlers.get(entity);
  if (!h) return; // its module has not registered yet (it does on import); the rows wait
  if (h.flush) {
    try {
      if (activeUser === user && isOnline()) await h.flush(user);
    } catch {
      // the entity keeps its rows; the next pass tries again
    }
    return;
  }
  for (;;) {
    if (activeUser !== user || !isOnline()) return;
    const due = outboxRows(user, entity).filter((r) => r.state === "queued" && !inflight.has(r.op_id));
    const head = due[0];
    if (!head) return;
    if (head.next_at > Date.now()) return; // FIFO: nothing overtakes a row that is backing off
    const { sendGroup, send } = h;
    if (sendGroup) {
      const group = due.filter((r) => r.group === head.group && r.next_at <= Date.now());
      const outcomes = await sendGuarded(group, () => sendGroup(head.group, group));
      let stop = false;
      for (const r of group) {
        const o = outcomes.get(r.op_id) ?? { kind: "retry", code: "no_answer" };
        if (await settle(h, r, o)) stop = true;
      }
      if (stop) return;
    } else if (send) {
      const outcome = (await sendGuarded([head], async () => new Map([[head.op_id, await send(head)]]))).get(
        head.op_id,
      ) ?? { kind: "retry", code: "no_answer" };
      if (await settle(h, head, outcome)) return;
    } else return;
  }
}

async function sendGuarded(
  batch: OutboxRow[],
  fn: () => Promise<Map<string, SendOutcome>>,
): Promise<Map<string, SendOutcome>> {
  for (const r of batch) {
    inflight.add(r.op_id);
    await put({ ...r, state: "sending", attempts: r.attempts + 1 });
  }
  try {
    return await fn();
  } catch {
    return new Map(batch.map((r) => [r.op_id, { kind: "unreachable" } as SendOutcome]));
  } finally {
    for (const r of batch) inflight.delete(r.op_id);
  }
}

/** Apply one answer. Returns true when the lane must stop (keep FIFO behind a row that will be retried). */
async function settle(h: EntityHandler, sent: OutboxRow, outcome: SendOutcome): Promise<boolean> {
  const current = rows.get(sent.op_id);
  try {
    h.settled?.(sent, outcome);
  } catch {
    // a cache refresh failing never stops the queue
  }
  if (!current) return false; // discarded while in flight
  if (outcome.kind === "ok" || outcome.kind === "conflict" || outcome.kind === "failed")
    waiters.get(sent.op_id)?.(outcome);
  const attempts = current.attempts;
  switch (outcome.kind) {
    case "ok":
    case "conflict": {
      if (outcome.kind === "conflict") {
        changedElsewhere.add(`${sent.entity}\u0000${sent.key}`);
        await keepConflict(sent, outcome.server);
      }
      // A row on the wire is never changed (a newer op queues behind it as its own row), so the answer clears it.
      await remove(sent.op_id);
      return false;
    }
    case "failed":
      await put({ ...current, state: "failed", last_error: outcome.code });
      return false;
    case "unreachable":
      await put({ ...current, state: "queued", last_error: "offline", next_at: Date.now() + backoffMs(attempts) });
      return true;
    case "retry":
      await put({ ...current, state: "queued", last_error: outcome.code, next_at: Date.now() + backoffMs(attempts) });
      return true;
  }
}

// ── Triggers ──

let wired = false;
/** Install the replay triggers once per page: online, visible again, every 30 s while rows wait. */
export function wireOutboxTriggers(): void {
  if (wired || typeof window === "undefined") return;
  wired = true;
  window.addEventListener("online", () => {
    // Coming back is the moment to try everything now, not after its backoff.
    for (const r of rows.values()) if (r.state === "queued" && r.next_at > 0) rows.set(r.op_id, { ...r, next_at: 0 });
    void replay();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void replay();
  });
  setInterval(() => {
    if (activeUser && outboxRows(activeUser).some((r) => r.state === "queued")) void replay();
  }, POLL_MS);
}

/** Wait until nothing of this user is queued or on the wire (parked rows don't count). Test and e2e helper. */
export function outboxIdle(user: string): boolean {
  return !outboxRows(user).some((r) => r.state !== "failed") && running === null;
}

/** Test hook: forget everything in memory (registered entity handlers stay: modules register them on import). */
export function resetOutboxForTests(): void {
  rows.clear();
  loaded.clear();
  inflight.clear();
  changedElsewhere.clear();
  waiters.clear();
  stopOutbox();
  running = null;
  rerun = false;
  syncing = 0;
  seqClock = 0;
  clock = () => Date.now();
  isOnline = () => true;
  emit();
}
