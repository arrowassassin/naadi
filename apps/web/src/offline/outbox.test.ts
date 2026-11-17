import "fake-indexeddb/auto";
import { Dexie } from "dexie";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DB_NAME, naadiDb, type OutboxRow, resetNaadiDbForTests } from "./db";
import {
  BACKOFF_MAX_MS,
  BACKOFF_MIN_MS,
  backoffMs,
  CONFLICT_KEEP_MS,
  conflictsFor,
  discardRow,
  enqueue,
  loadOutbox,
  outboxRows,
  outboxStatus,
  registerEntity,
  replay,
  resetOutboxForTests,
  retryRow,
  type SendOutcome,
  setOnlineProbe,
  startOutbox,
  stopOutbox,
  submit,
  wasChangedElsewhere,
} from "./outbox";

const user = "u-1";
type Script = (row: OutboxRow) => SendOutcome | Promise<SendOutcome>;

/** A fake LWW entity and a fake append entity whose answers each test scripts. */
let lww: Script = () => ({ kind: "ok" });
let app: Script = () => ({ kind: "ok" });
const sent: string[] = [];

registerEntity({
  entity: "cost",
  coalesce: "replace",
  send: async (row) => {
    sent.push(`cost:${row.key}:${(row.body as { v: number }).v}`);
    return lww(row);
  },
});
registerEntity({
  entity: "card_review",
  coalesce: "append",
  send: async (row) => {
    sent.push(`review:${row.key}`);
    return app(row);
  },
});
registerEntity({
  entity: "user_pathway_task",
  coalesce: "replace",
  after: ["cost"],
  send: async (row) => {
    sent.push(`task:${row.key}`);
    return { kind: "ok" };
  },
});

beforeEach(async () => {
  resetOutboxForTests();
  resetNaadiDbForTests();
  await Dexie.delete(DB_NAME);
  sent.length = 0;
  lww = () => ({ kind: "ok" });
  app = () => ({ kind: "ok" });
});
afterEach(() => {
  stopOutbox();
  vi.useRealTimers();
});

const cost = (key: string, v: number) => ({ entity: "cost" as const, key, method: "PATCH" as const, body: { v } });
const review = (key: string) => ({ entity: "card_review" as const, key, method: "POST" as const, body: {} });

describe("queueing", () => {
  it("coalesces LWW ops per key and keeps every append op, in FIFO order", async () => {
    setOnlineProbe(() => false);
    await enqueue(user, cost("c1", 1));
    await enqueue(user, cost("c1", 2));
    await enqueue(user, cost("c2", 1));
    await enqueue(user, review("r1"));
    await enqueue(user, review("r1"));
    const rows = outboxRows(user);
    expect(rows.map((r) => `${r.entity}:${r.key}`)).toEqual(["cost:c1", "cost:c2", "card_review:r1", "card_review:r1"]);
    expect(rows[0]?.body).toEqual({ v: 2 });
    expect(rows[0]?.rev).toBe(2);
    expect(outboxStatus(user)).toEqual({ pending: 4, failed: 0, syncing: false });
    expect(outboxStatus("someone-else").pending).toBe(0);
  });

  it("writes every op to IndexedDB before any request, and reloads it (a row cut off mid-send is sent again)", async () => {
    setOnlineProbe(() => false);
    await enqueue(user, cost("c1", 1));
    const stored = await naadiDb().outbox.toArray();
    expect(stored).toHaveLength(1);
    await naadiDb().outbox.put({ ...(stored[0] as OutboxRow), state: "sending" });
    resetOutboxForTests();
    await loadOutbox(user);
    expect(outboxRows(user).map((r) => r.state)).toEqual(["queued"]);
  });

  it("gives a changed op a new Idempotency-Key once it was sent (the server caches answers by key)", async () => {
    await startOutbox(user);
    lww = () => ({ kind: "unreachable" });
    const first = await enqueue(user, cost("c1", 1));
    await replay();
    const afterSend = outboxRows(user)[0] as OutboxRow;
    expect(afterSend.op_id).toBe(first?.op_id);
    expect(afterSend.attempts).toBe(1);
    const changed = await enqueue(user, cost("c1", 2));
    expect(changed?.op_id).not.toBe(first?.op_id);
    expect(outboxRows(user)).toHaveLength(1);
    // Never sent: the key is kept while it changes.
    setOnlineProbe(() => false);
    const fresh = await enqueue(user, cost("c9", 1));
    expect((await enqueue(user, cost("c9", 2)))?.op_id).toBe(fresh?.op_id);
  });

  it("never changes a row whose request is on the wire: the newer op queues behind it", async () => {
    await startOutbox(user);
    let release: (o: SendOutcome) => void = () => {};
    lww = () =>
      new Promise((r) => {
        release = r;
      });
    await enqueue(user, cost("c1", 1));
    const running = replay();
    await vi.waitFor(() => expect(sent).toEqual(["cost:c1:1"]));
    await enqueue(user, cost("c1", 2));
    expect(outboxRows(user).map((r) => (r.body as { v: number }).v)).toEqual([1, 2]);
    lww = () => ({ kind: "ok" });
    release({ kind: "ok" });
    await running;
    await replay();
    expect(sent).toEqual(["cost:c1:1", "cost:c1:2"]);
    expect(outboxRows(user)).toEqual([]);
  });
});

describe("replay", () => {
  it("is FIFO per entity, parallel across entities, and waits for the entities it depends on", async () => {
    await startOutbox(user);
    setOnlineProbe(() => false);
    await enqueue(user, { entity: "user_pathway_task", key: "t1", method: "PATCH", body: {} });
    await enqueue(user, review("r1"));
    await enqueue(user, cost("c1", 1));
    await enqueue(user, review("r2"));
    setOnlineProbe(() => true);
    await replay();
    expect(sent.filter((s) => s.startsWith("review"))).toEqual(["review:r1", "review:r2"]);
    expect(sent.indexOf("cost:c1:1")).toBeLessThan(sent.indexOf("task:t1"));
    expect(outboxRows(user)).toEqual([]);
  });

  it("stops an entity's lane behind a row that backs off; nothing overtakes it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await startOutbox(user);
    app = (row) => (row.key === "r1" ? { kind: "retry", code: "http_503" } : { kind: "ok" });
    setOnlineProbe(() => false);
    await enqueue(user, review("r1"));
    await enqueue(user, review("r2"));
    setOnlineProbe(() => true);
    await replay();
    expect(sent).toEqual(["review:r1"]);
    const head = outboxRows(user)[0] as OutboxRow;
    expect(head.last_error).toBe("http_503");
    expect(head.next_at).toBeGreaterThan(Date.now());
    await replay();
    expect(sent).toEqual(["review:r1"]); // still backing off
    app = () => ({ kind: "ok" });
    vi.setSystemTime(Date.now() + BACKOFF_MAX_MS * 2);
    await replay();
    expect(sent).toEqual(["review:r1", "review:r1", "review:r2"]);
  });

  it("parks a refused row as failed (visible, never retried on its own) and moves on; Retry and Discard", async () => {
    await startOutbox(user);
    app = (row) => (row.key === "bad" ? { kind: "failed", code: "card_not_found" } : { kind: "ok" });
    setOnlineProbe(() => false);
    await enqueue(user, review("bad"));
    await enqueue(user, review("good"));
    setOnlineProbe(() => true);
    await replay();
    expect(sent).toEqual(["review:bad", "review:good"]);
    expect(outboxStatus(user)).toMatchObject({ pending: 0, failed: 1 });
    const parked = outboxRows(user)[0] as OutboxRow;
    expect(parked).toMatchObject({ state: "failed", last_error: "card_not_found" });
    await replay();
    expect(sent).toHaveLength(2);
    app = () => ({ kind: "ok" });
    await retryRow(parked.op_id);
    await replay();
    expect(outboxRows(user)).toEqual([]);
    // Discard keeps a readable copy in the conflict log.
    app = () => ({ kind: "failed", code: "card_not_found" });
    await enqueue(user, review("again"));
    await replay();
    await discardRow((outboxRows(user)[0] as OutboxRow).op_id);
    expect(outboxRows(user)).toEqual([]);
    expect((await conflictsFor(user)).map((c) => c.key)).toEqual(["again"]);
  });

  it("keeps the losing local version of an LWW race for 7 days and marks the row for this session", async () => {
    await startOutbox(user);
    lww = () => ({ kind: "conflict", server: { v: 99 } });
    await enqueue(user, cost("c1", 1));
    await replay();
    expect(outboxRows(user)).toEqual([]);
    expect(wasChangedElsewhere("cost", "c1")).toBe(true);
    expect(wasChangedElsewhere("cost", "c2")).toBe(false);
    const kept = await conflictsFor(user);
    expect(kept).toHaveLength(1);
    expect(kept[0]).toMatchObject({ entity: "cost", key: "c1", local: { v: 1 }, server: { v: 99 } });
    // Older than 7 days: purged on the next load.
    await naadiDb().conflicts.add({
      ...(kept[0] as object),
      id: undefined,
      at: Date.now() - CONFLICT_KEEP_MS - 1,
    } as never);
    resetOutboxForTests();
    await loadOutbox(user);
    expect(await conflictsFor(user)).toHaveLength(1);
  });

  it("does nothing offline and sends nothing for another user", async () => {
    await startOutbox(user);
    setOnlineProbe(() => false);
    await enqueue(user, cost("c1", 1));
    await enqueue("other", cost("c2", 1));
    await replay();
    expect(sent).toEqual([]);
    setOnlineProbe(() => true);
    await replay();
    expect(sent).toEqual(["cost:c1:1"]);
    expect(outboxRows("other")).toHaveLength(1);
  });

  it("an unreachable server stops the lane and keeps the row", async () => {
    await startOutbox(user);
    lww = () => ({ kind: "unreachable" });
    await enqueue(user, cost("c1", 1));
    await enqueue(user, cost("c2", 1));
    await replay();
    expect(sent).toEqual(["cost:c1:1"]);
    expect(outboxRows(user).map((r) => r.last_error)).toEqual(["offline", null]);
  });

  it("submit answers online at once and says null (queued) offline", async () => {
    await startOutbox(user);
    lww = () => ({ kind: "ok", server: { stored: true } });
    expect((await submit(user, cost("c1", 1))).outcome).toEqual({ kind: "ok", server: { stored: true } });
    setOnlineProbe(() => false);
    expect((await submit(user, cost("c2", 1))).outcome).toBeNull();
    expect(outboxRows(user)).toHaveLength(1);
  });
});

describe("backoff", () => {
  it("doubles from 2 s to a 5 min ceiling with ±20 % jitter", () => {
    expect(backoffMs(1, () => 0.5)).toBe(BACKOFF_MIN_MS);
    expect(backoffMs(2, () => 0.5)).toBe(BACKOFF_MIN_MS * 2);
    expect(backoffMs(30, () => 0.5)).toBe(BACKOFF_MAX_MS);
    expect(backoffMs(1, () => 0)).toBe(BACKOFF_MIN_MS * 0.8);
    expect(backoffMs(1, () => 1)).toBe(BACKOFF_MIN_MS * 1.2);
  });
});
