import "fake-indexeddb/auto";
import { blockResolver } from "@naadi/ink";
import { type InkDiff, type InkItem, type InkStroke, mergeInkItems, uuidv7 } from "@naadi/shared";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dexieStore, inkDb, resetInkDbForTests } from "./db";
import { hasOpaqueBlocks } from "./SectionInk";
import {
  beats,
  type ChangesPayload,
  chunk,
  InkSync,
  type InkTransport,
  KEEPALIVE_BYTES,
  type LayerPayload,
} from "./sync";

const T = 1_763_000_000_000;

function stroke(over: Partial<InkStroke> = {}): InkStroke {
  return {
    id: uuidv7(),
    tool: "pen",
    color: "ink",
    size: 0.002,
    points: [
      [0.1, 0.1, 0.5, 0],
      [0.2, 0.2, 0.5, 8],
    ],
    createdAt: T,
    updatedAt: T,
    ...over,
  };
}

/** An in-memory server with the API's merge (higher updatedAt, tombstone on a tie, stored row on a full tie). */
class FakeServer {
  rows = new Map<string, { item: InkItem; seq: number }>();
  seq = 0;
  offline = false;
  sent: InkItem[][] = [];
  beacons: InkItem[][] = [];
  /** The server's clock: stamps more than 5 minutes past it are clamped and echoed, as the API does. */
  now = () => T + 1_000;
  /** Ids the server refuses as invalid. */
  invalid = new Set<string>();
  transport(): InkTransport {
    return {
      load: async (since) => {
        if (this.offline) throw new TypeError("Failed to fetch");
        const all = [...this.rows.values()];
        const items =
          since === null
            ? all.filter((r) => !r.item.deleted).map((r) => r.item)
            : all.filter((r) => r.seq > since).map((r) => r.item);
        return { rev: this.seq, items, reset: false, outdated: 0 } satisfies LayerPayload;
      },
      send: async (base, items) => {
        if (this.offline) throw new TypeError("Failed to fetch");
        this.sent.push(items);
        const applied: string[] = [];
        const rejected: ChangesPayload["rejected"] = [];
        const clamped = new Set<string>();
        for (const sent of items) {
          if (this.invalid.has(sent.id)) {
            rejected.push({ id: sent.id, reason: "invalid" });
            continue;
          }
          let it = sent;
          if (it.updatedAt > this.now() + 300_000) {
            it = { ...it, updatedAt: this.now() };
            clamped.add(it.id);
          }
          const cur = this.rows.get(it.id);
          if (!cur || beats(it, cur.item)) {
            this.rows.set(it.id, { item: it, seq: ++this.seq });
            applied.push(it.id);
          } else rejected.push({ id: it.id, reason: "stale" });
        }
        const mine = new Set(applied);
        const remote = [...this.rows.values()]
          .filter(
            (r) =>
              (r.seq > base && !mine.has(r.item.id)) ||
              rejected.some((x) => x.id === r.item.id) ||
              (clamped.has(r.item.id) && mine.has(r.item.id)),
          )
          .map((r) => r.item);
        return { rev: this.seq, applied, rejected, remote, reset: false };
      },
      beacon: (_base, items) => {
        this.beacons.push(items);
        return true;
      },
    };
  }
}

function make(
  server: FakeServer,
  surface = "section:s1",
  over: Partial<ConstructorParameters<typeof InkSync>[0]> = {},
) {
  const remote: InkItem[][] = [];
  const adopted: InkItem[][] = [];
  const s = new InkSync({
    transport: server.transport(),
    store: dexieStore("u1", surface),
    onRemote: (items, o) => (o?.authoritative ? adopted : remote).push(items),
    classify: () => "offline",
    setTimer: () => 0, // flushes are driven by the tests
    clearTimer: () => undefined,
    ...over,
  });
  return { s, remote, adopted };
}

const add = (...items: InkItem[]): InkDiff => ({ added: items, updated: [], removed: [] });

beforeEach(async () => {
  resetInkDbForTests();
  await inkDb().delete(); // Dexie keeps the IndexedDB factory it saw at import: empty the database instead
  resetInkDbForTests();
});
afterEach(() => resetInkDbForTests());

/** Manual timers: fake-indexeddb runs on real timers, so the clock is driven by hand. */
function manualClock() {
  let now = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  let next = 1;
  return {
    setTimer: (fn: () => void, ms: number) => {
      const id = next++;
      timers.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimer: (id: unknown) => void timers.delete(id as number),
    async advance(ms: number) {
      now += ms;
      for (const [id, t] of [...timers]) {
        if (t.at <= now) {
          timers.delete(id);
          t.fn();
        }
      }
      for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0)); // let the flush finish
    },
  };
}

describe("pending map", () => {
  it("keeps only the latest version per id and turns removals into tombstones", async () => {
    const { s } = make(new FakeServer());
    await s.restore();
    const a = stroke();
    s.record(add(a));
    const moved = { ...a, points: [[0.3, 0.3, 0.5, 0]] as InkStroke["points"], updatedAt: T + 5 };
    s.record({ added: [], updated: [moved], removed: [] });
    expect(s.pendingCount).toBe(1);
    s.record({ added: [], updated: [], removed: [a.id] });
    const pending = await dexieStore("u1", "section:s1").readPending();
    expect(pending).toHaveLength(1);
    expect(pending[0]?.deleted).toBe(true);
    expect(pending[0]?.updatedAt).toBeGreaterThan(moved.updatedAt);
  });

  it("is on the device before the network is used, and survives a reload", async () => {
    const server = new FakeServer();
    server.offline = true;
    const first = make(server);
    await first.s.restore();
    const a = stroke();
    const b = stroke({ color: "violet" });
    first.s.record(add(a, b));
    await first.s.flush();
    expect(first.s.state).toBe("local"); // "Saved on this device · will sync"
    first.s.stop();
    // Reload: a new tab, the same device.
    resetInkDbForTests();
    const second = make(server);
    const shown = await second.s.restore();
    expect(shown.map((i) => i.id).sort()).toEqual([a.id, b.id].sort());
    expect(second.s.pendingCount).toBe(2);
    server.offline = false;
    await second.s.refresh(); // reconnect: load, then resend what is pending
    expect(second.s.state).toBe("saved");
    expect([...server.rows.keys()].sort()).toEqual([a.id, b.id].sort());
    expect(await dexieStore("u1", "section:s1").readPending()).toEqual([]);
  });

  it("a confirmed send keeps a newer local edit of the same item pending", async () => {
    const server = new FakeServer();
    const { s } = make(server);
    await s.restore();
    const a = stroke();
    s.record(add(a));
    const sending = s.flush();
    s.record({ added: [], updated: [{ ...a, color: "lime", updatedAt: T + 9 }], removed: [] });
    await sending;
    await s.flush();
    expect(server.rows.get(a.id)?.item.color).toBe("lime");
    expect(s.pendingCount).toBe(0);
  });
});

describe("flush triggers", () => {
  it("after 800 ms of quiet, or at once at 50 items", async () => {
    const clock = manualClock();
    const server = new FakeServer();
    const { s } = make(server, "section:s2", { setTimer: clock.setTimer, clearTimer: clock.clearTimer });
    await s.restore();
    s.record(add(stroke()));
    await clock.advance(700);
    expect(server.sent).toHaveLength(0);
    s.record(add(stroke())); // restarts the idle timer
    await clock.advance(700);
    expect(server.sent).toHaveLength(0);
    await clock.advance(150);
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toHaveLength(2);
    s.record(add(...Array.from({ length: 50 }, () => stroke())));
    await clock.advance(0);
    expect(server.sent).toHaveLength(2);
    s.stop();
  });

  it("pagehide starts one keepalive request under 64 KB; the rest stays on the device", async () => {
    const server = new FakeServer();
    const { s } = make(server);
    await s.restore();
    const big = Array.from({ length: 40 }, () =>
      stroke({
        points: Array.from({ length: 200 }, (_, i) => [0.123456, 0.654321, 0.5, i] as [number, number, number, number]),
      }),
    );
    s.record(add(...big));
    s.beacon();
    expect(server.beacons).toHaveLength(1);
    const body = JSON.stringify({ base_rev: 0, items: server.beacons[0] });
    expect(body.length).toBeLessThan(64 * 1024);
    expect(server.beacons[0]?.length).toBeLessThan(big.length);
    expect(s.pendingCount).toBe(big.length); // nothing acknowledged without an answer
    expect(chunk(big, KEEPALIVE_BYTES).length).toBe(server.beacons[0]?.length);
  });
});

describe("remote changes", () => {
  it("hands the engine other devices' versions, keeps newer local ones pending", async () => {
    const server = new FakeServer();
    const { s, remote } = make(server);
    await s.restore();
    await s.refresh();
    const theirs = stroke();
    const shared = stroke();
    server.rows.set(theirs.id, { item: theirs, seq: ++server.seq });
    server.rows.set(shared.id, { item: shared, seq: ++server.seq });
    await s.refresh();
    expect(
      remote
        .flat()
        .map((i) => i.id)
        .sort(),
    ).toEqual([theirs.id, shared.id].sort());
    // Local edit newer than the server's copy wins and is sent; a later remote copy that is older is ignored.
    const mine = { ...shared, color: "violet" as const, updatedAt: T + 50 };
    s.record({ added: [], updated: [mine], removed: [] });
    server.rows.set(shared.id, { item: { ...shared, color: "amber", updatedAt: T + 10 }, seq: ++server.seq });
    remote.length = 0;
    await s.refresh();
    expect(remote.flat()).toEqual([]);
    expect(server.rows.get(shared.id)?.item.color).toBe("violet");
  });

  it("delete on one device, edit on the other: both converge on the LWW result", async () => {
    const server = new FakeServer();
    const A = make(server, "section:s3");
    const B = make(server, "section:s3");
    const x = stroke();
    await A.s.restore();
    A.s.record(add(x));
    await A.s.flush();
    await B.s.restore();
    await B.s.refresh();
    A.s.record({ added: [], updated: [], removed: [x.id] }); // tombstone stamped "now" (later than T)
    B.s.record({ added: [], updated: [{ ...x, color: "lime", updatedAt: T + 1 }], removed: [] });
    await B.s.flush();
    await A.s.flush();
    await B.s.refresh();
    await A.s.refresh();
    const left = A.s.replicaItems().find((i) => i.id === x.id);
    const right = B.s.replicaItems().find((i) => i.id === x.id);
    expect(left?.deleted).toBe(true);
    expect(right).toEqual(left);
    expect(mergeInkItems(A.s.replicaItems(), B.s.replicaItems()).find((i) => i.id === x.id)?.deleted).toBe(true);
  });

  it("the device snapshot shows synced ink on an offline reload", async () => {
    const server = new FakeServer();
    const synced = stroke();
    server.rows.set(synced.id, { item: synced, seq: ++server.seq });
    const first = make(server);
    await first.s.restore();
    await first.s.refresh();
    await new Promise((r) => setTimeout(r, 0));
    resetInkDbForTests();
    server.offline = true;
    const second = make(server);
    expect((await second.s.restore()).map((i) => i.id)).toEqual([synced.id]);
    expect((await inkDb().inkLayers.toArray())[0]?.rev).toBe(1);
  });
});

describe("block anchors across widths", () => {
  it("the same paragraph, two layouts: an anchored stroke follows its block", () => {
    const landscape = blockResolver([
      { pm: 0, y: 0, fp: "heading:00000001" },
      { pm: 31, y: 0.12, fp: "paragraph:00000002" },
      { pm: 80, y: 0.31, fp: "paragraph:00000003" },
    ]);
    const phone = blockResolver([
      { pm: 0, y: 0, fp: "heading:00000001" },
      { pm: 31, y: 0.38, fp: "paragraph:00000002" },
      { pm: 80, y: 1.02, fp: "paragraph:00000003" },
    ]);
    const a = landscape?.at(0.33);
    expect(a?.pm).toBe(80);
    expect(a?.fp).toBe("paragraph:00000003");
    expect((phone?.offset(80, a?.fp) ?? 0) + (a?.dy ?? 0)).toBeCloseTo(1.04, 6);
    expect(phone?.offset(80, "paragraph:0000beef")).toBeNull(); // the paragraph was edited: no snapping
    expect(phone?.offset(80, undefined)).toBeNull();
  });
});

describe("section marker placement", () => {
  it("multiplies markers over tables and figures, sits under plain text", () => {
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "paragraph" }, { type: "table" }] })).toBe(true);
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "figure" }] })).toBe(true);
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "paragraph" }, { type: "heading" }] })).toBe(false);
    expect(hasOpaqueBlocks(null)).toBe(false);
  });
});

describe("review fixes", () => {
  it("P1: a clock running ahead adopts the server's stamp, so a deletion on another device sticks", async () => {
    const server = new FakeServer();
    const ahead = () => T + 1_000 + 600_000; // device A is 10 minutes fast
    const A = make(server, "section:s1", { now: ahead });
    const B = make(server, "section:s1");
    await A.s.restore();
    await B.s.restore();
    const x = stroke({ updatedAt: ahead() });
    A.s.record(add(x));
    await A.s.flush();
    // The server clamped X to its own time and sent its copy back: A adopted it (authoritative), stamp included.
    expect(server.rows.get(x.id)?.item.updatedAt).toBe(T + 1_000);
    expect(A.adopted.flat().map((i) => [i.id, i.updatedAt])).toEqual([[x.id, T + 1_000]]);
    expect(A.s.replicaItems().find((i) => i.id === x.id)?.updatedAt).toBe(T + 1_000);
    // B erases X a moment later (server time); A's next refresh brings the tombstone and A drops X.
    await B.s.refresh();
    B.s.record({ added: [], updated: [], removed: [x.id] });
    expect(server.now() + 5).toBeGreaterThan(T + 1_000);
    await B.s.flush();
    expect(server.rows.get(x.id)?.item.deleted).toBe(true);
    await A.s.refresh();
    expect(A.s.liveItems().map((i) => i.id)).not.toContain(x.id);
    expect(A.remote.flat().some((i) => i.id === x.id && i.deleted)).toBe(true);
    expect(server.rows.get(x.id)?.item.deleted).toBe(true);
  });

  it("P1: a newer local edit made while the clamped write was in flight is not overwritten", async () => {
    const server = new FakeServer();
    server.now = () => T + 1_000 + server.sent.length; // the server's clock moves between requests
    const t = server.transport();
    let release: () => void = () => undefined;
    let started: () => void = () => undefined;
    const inFlight = new Promise<void>((r) => {
      started = r;
    });
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const A = make(server, "section:s1", {
      now: () => T + 2_000_000,
      transport: {
        ...t,
        send: async (b, items, o) => {
          started();
          await gate;
          return t.send(b, items, o);
        },
      },
    });
    await A.s.restore();
    const x = stroke({ updatedAt: T + 2_000_000 });
    A.s.record(add(x));
    const flushing = A.s.flush();
    await inFlight;
    A.s.record({ added: [], updated: [{ ...x, color: "violet", updatedAt: T + 2_000_001 }], removed: [] });
    release();
    await flushing;
    // The first answer's copy (the ink colour) was not adopted over the newer violet edit; the violet one was sent
    // next and is the one adopted.
    expect(A.adopted.flat().map((i) => i.color)).toEqual(["violet"]);
    expect(A.s.replicaItems().find((i) => i.id === x.id)?.color).toBe("violet");
    expect(A.s.pendingCount).toBe(0);
  });

  it("P5: an item the server refuses is quarantined on the device, shown as an error, and can be retried or discarded", async () => {
    const server = new FakeServer();
    const A = make(server);
    await A.s.restore();
    const bad = stroke();
    const good = stroke();
    server.invalid.add(bad.id);
    A.s.record(add(bad, good));
    await A.s.flush();
    expect(A.s.state).toBe("error");
    expect(A.s.lastFailure).toBe("rejected");
    expect(A.s.rejectedCount).toBe(1);
    expect(A.s.pendingCount).toBe(0); // the rest of the queue is not blocked
    expect(server.rows.has(good.id)).toBe(true);
    expect(A.s.liveItems().map((i) => i.id)).toContain(bad.id); // still on the canvas
    expect((await inkDb().inkRejected.toArray()).map((r) => r.id)).toEqual([bad.id]);
    expect(await inkDb().inkPending.count()).toBe(0);
    // A reload keeps it quarantined (and on the canvas).
    const again = make(server);
    expect((await again.s.restore()).map((i) => i.id)).toContain(bad.id);
    expect(again.s.state).toBe("error");
    // Retry sends it again; the server now takes it.
    server.invalid.clear();
    await again.s.retry({ rejected: true });
    expect(server.rows.has(bad.id)).toBe(true);
    expect(again.s.state).toBe("saved");
    expect(await inkDb().inkRejected.count()).toBe(0);
  });

  it("P5: Discard removes the quarantined item from the canvas and the device", async () => {
    const server = new FakeServer();
    const A = make(server);
    await A.s.restore();
    const bad = stroke();
    server.invalid.add(bad.id);
    A.s.record(add(bad));
    await A.s.flush();
    A.s.discardRejected();
    expect(A.s.state).toBe("saved");
    expect(A.s.liveItems()).toEqual([]);
    expect(A.remote.flat()).toEqual([{ ...bad, deleted: true }]);
    await new Promise((r) => setTimeout(r, 0));
    expect(await inkDb().inkRejected.count()).toBe(0);
    expect(server.rows.has(bad.id)).toBe(false);
  });

  it("P3: a stale edit with reset (purged deletion) leaves the device without the stroke", async () => {
    const server = new FakeServer();
    const A = make(server);
    await A.s.restore();
    const x = stroke();
    const t = server.transport();
    // The server answers like the API below the purge horizon: stale, reset, a full list without X.
    A.s.record(add(x));
    const keep = stroke();
    server.rows.set(keep.id, { item: keep, seq: ++server.seq });
    const s2 = make(server, "section:s1", {
      transport: {
        ...t,
        send: async () => ({
          rev: server.seq,
          applied: [],
          rejected: [{ id: x.id, reason: "stale" }],
          remote: [keep],
          reset: true,
        }),
      },
    });
    s2.s.record(add(x));
    await s2.s.restore();
    await s2.s.flush();
    expect(s2.s.liveItems().map((i) => i.id)).toEqual([keep.id]);
    expect(s2.remote.flat().some((i) => i.id === x.id && i.deleted)).toBe(true);
    expect(s2.s.pendingCount).toBe(0);
  });
});
