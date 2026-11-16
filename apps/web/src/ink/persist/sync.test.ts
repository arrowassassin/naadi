import "fake-indexeddb/auto";
import { blockResolver } from "@naadi/ink";
import { type InkDiff, type InkItem, type InkStroke, mergeInkItems, uuidv7 } from "@naadi/shared";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dexieStore, inkDb, resetInkDbForTests } from "./db";
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
        for (const it of items) {
          const cur = this.rows.get(it.id);
          if (!cur || beats(it, cur.item)) {
            this.rows.set(it.id, { item: it, seq: ++this.seq });
            applied.push(it.id);
          } else rejected.push({ id: it.id, reason: "stale" });
        }
        const mine = new Set(applied);
        const remote = [...this.rows.values()]
          .filter((r) => (r.seq > base && !mine.has(r.item.id)) || rejected.some((x) => x.id === r.item.id))
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
  const s = new InkSync({
    transport: server.transport(),
    store: dexieStore("u1", surface),
    onRemote: (items) => remote.push(items),
    classify: () => "offline",
    setTimer: () => 0, // flushes are driven by the tests
    clearTimer: () => undefined,
    ...over,
  });
  return { s, remote };
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
      { pm: 0, y: 0 },
      { pm: 31, y: 0.12 },
      { pm: 80, y: 0.31 },
    ]);
    const phone = blockResolver([
      { pm: 0, y: 0 },
      { pm: 31, y: 0.38 },
      { pm: 80, y: 1.02 },
    ]);
    const a = landscape?.at(0.33);
    expect(a?.pm).toBe(80);
    expect((phone?.offset(80) ?? 0) + (a?.dy ?? 0)).toBeCloseTo(1.04, 6);
  });
});

describe("section marker placement", async () => {
  const { hasOpaqueBlocks } = await import("./SectionInk");
  it("multiplies markers over tables and figures, sits under plain text", () => {
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "paragraph" }, { type: "table" }] })).toBe(true);
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "figure" }] })).toBe(true);
    expect(hasOpaqueBlocks({ type: "doc", content: [{ type: "paragraph" }, { type: "heading" }] })).toBe(false);
    expect(hasOpaqueBlocks(null)).toBe(false);
  });
});
