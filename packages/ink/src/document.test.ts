import { type InkDiff, type InkItem, mergeInkItems, recordInkDiff } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { InkDocument } from "./document";
import { stroke } from "./test/helpers";

/** An item without its updatedAt (undo/redo restamp it). */
function plain(i: InkItem | undefined) {
  if (!i) return i;
  const { updatedAt: _u, ...rest } = i;
  return rest;
}

function setup() {
  const doc = new InkDocument();
  const diffs: InkDiff[] = [];
  doc.onDiff((d) => diffs.push(d));
  return { doc, diffs };
}

describe("InkDocument", () => {
  it("add, undo, redo emit one diff each and restore state", () => {
    const { doc, diffs } = setup();
    const a = stroke([
      [0.1, 0.1],
      [0.2, 0.2],
    ]);
    doc.commit({ before: [], after: doc.fresh([a]) });
    expect(doc.items()).toEqual([a]);
    doc.undo();
    expect(doc.size).toBe(0);
    doc.redo();
    expect(doc.items().map(plain)).toEqual([plain(a)]); // redo restamps updatedAt
    expect(diffs).toEqual([
      { added: [a], updated: [], removed: [] },
      { added: [], updated: [], removed: [a.id] },
      { added: [{ ...a, updatedAt: expect.any(Number) }], updated: [], removed: [] },
    ]);
  });

  it("a new action clears redo", () => {
    const { doc } = setup();
    doc.commit({ before: [], after: doc.fresh([stroke([[0, 0]])]) });
    doc.undo();
    expect(doc.canRedo()).toBe(true);
    doc.commit({ before: [], after: doc.fresh([stroke([[0.5, 0.5]])]) });
    expect(doc.canRedo()).toBe(false);
  });

  it("an update keeps z-order and reports `updated`", () => {
    const { doc, diffs } = setup();
    const a = stroke([[0.1, 0.1]]);
    const b = stroke([[0.2, 0.2]]);
    doc.commit({ before: [], after: doc.fresh([a, b]) });
    const ea = doc.entry(a.id);
    if (!ea) throw new Error("missing");
    const moved = { ...a, color: "violet" as const, updatedAt: a.updatedAt + 1 };
    doc.commit({ before: [ea], after: [{ item: moved, seq: ea.seq }] });
    expect(doc.items().map((i) => i.id)).toEqual([a.id, b.id]);
    expect(diffs.at(-1)).toEqual({ added: [], updated: [moved], removed: [] });
    doc.undo();
    expect(plain(doc.get(a.id))).toEqual(plain(a));
  });

  it("a transaction (pixel erase) is one undoable action with one net diff", () => {
    const { doc, diffs } = setup();
    const a = stroke([
      [0.1, 0.5],
      [0.9, 0.5],
    ]);
    doc.commit({ before: [], after: doc.fresh([a]) });
    diffs.length = 0;
    doc.begin();
    const ea = doc.entry(a.id);
    if (!ea) throw new Error("missing");
    const f1 = stroke([[0.1, 0.5]]);
    const f2 = stroke([[0.9, 0.5]]);
    doc.commit({
      before: [ea],
      after: [
        { item: f1, seq: ea.seq + 0.3 },
        { item: f2, seq: ea.seq + 0.6 },
      ],
    });
    const e2 = doc.entry(f2.id);
    if (!e2) throw new Error("missing");
    doc.commit({ before: [e2], after: [] }); // the second fragment is erased in the same gesture
    expect(diffs).toEqual([]);
    const diff = doc.end();
    expect(diff).toEqual({ added: [f1], updated: [], removed: [a.id] });
    expect(diffs).toHaveLength(1);
    doc.undo();
    expect(doc.items().map(plain)).toEqual([plain(a)]);
    doc.redo();
    expect(doc.items().map(plain)).toEqual([plain(f1)]);
  });

  it("rollback restores everything a transaction touched", () => {
    const { doc, diffs } = setup();
    const a = stroke([[0.1, 0.5]]);
    doc.commit({ before: [], after: doc.fresh([a]) });
    doc.begin();
    const ea = doc.entry(a.id);
    if (!ea) throw new Error("missing");
    doc.commit({ before: [ea], after: [] });
    expect(doc.size).toBe(0);
    doc.rollback();
    expect(doc.items()).toEqual([a]);
    expect(diffs).toHaveLength(1);
    expect(doc.canUndo()).toBe(true);
    doc.undo();
    expect(doc.size).toBe(0);
  });

  it("load validates, drops tombstones and resets history", () => {
    const { doc } = setup();
    const a = stroke([[0.1, 0.1]]);
    const dead = { ...stroke([[0.2, 0.2]]), deleted: true as const };
    doc.load([a, dead]);
    expect(doc.items()).toEqual([a]);
    expect(doc.canUndo()).toBe(false);
    expect(() => doc.load([{ ...a, size: -1 }])).toThrow();
  });

  it("queries in z-order through the spatial index", () => {
    const { doc } = setup();
    const a = stroke([[0.5, 0.5]]);
    const b = stroke([[0.51, 0.5]]);
    const far = stroke([[0.1, 3]]);
    doc.commit({ before: [], after: doc.fresh([a, b, far]) });
    expect(doc.query({ x0: 0.4, y0: 0.4, x1: 0.6, y1: 0.6 }).map((e) => e.item.id)).toEqual([a.id, b.id]);
  });
});

describe("undo/redo and last-writer-wins sync", () => {
  it("undo/redo re-stamp what they bring back, so a replica that saw the intermediate state cannot win", () => {
    let clock = 1_000;
    const doc = new InkDocument({ now: () => clock });
    const diffs: InkDiff[] = [];
    // Local replica as 12b/18 keep it: every diff recorded with tombstones.
    let local: InkItem[] = [];
    doc.onDiff((d) => {
      diffs.push(d);
      local = recordInkDiff(local, d, clock);
    });

    const s = stroke([[0.1, 0.1]], { createdAt: 1_000, updatedAt: 1_000 });
    doc.commit({ before: [], after: doc.fresh([s]) });
    clock = 2_000;
    const e = doc.entry(s.id);
    if (!e) throw new Error("missing");
    doc.commit({ before: [e], after: [] }); // delete
    // Another device synced right now: it holds the tombstone from t=2000.
    const remote = [...local];
    expect(remote[0]?.deleted).toBe(true);

    clock = 3_000;
    doc.undo(); // bring the stroke back
    expect(diffs.at(-1)?.added[0]?.updatedAt).toBe(3_000);
    const merged = mergeInkItems(local, remote);
    expect(merged.find((i) => i.id === s.id)?.deleted).toBeUndefined();

    clock = 4_000;
    doc.redo(); // delete again: the new tombstone is newer than the restore
    const merged2 = mergeInkItems(local, merged);
    expect(merged2.find((i) => i.id === s.id)).toMatchObject({ deleted: true, updatedAt: 4_000 });

    clock = 5_000;
    doc.undo();
    expect(diffs.at(-1)?.added[0]?.updatedAt).toBe(5_000);
    expect(mergeInkItems(local, merged2).find((i) => i.id === s.id)?.deleted).toBeUndefined();
  });

  it("stamps repeated edits after the previous version even when the clock moves back", () => {
    const doc = new InkDocument({ now: () => 900 });
    const item = stroke([[0.1, 0.1]], { createdAt: 1_000, updatedAt: 1_000 });
    doc.commit({ before: [], after: doc.fresh([item]) });
    const before = doc.entry(item.id);
    if (!before) throw new Error("missing stroke");
    const change = doc.commit({
      before: [before],
      after: [{ ...before, item: { ...item, color: "violet", updatedAt: 900 } }],
    });
    expect(change?.updated).toEqual([{ ...item, color: "violet", updatedAt: 1_001 }]);
  });

  it("undo of a move restamps the old position", () => {
    let clock = 10;
    const doc = new InkDocument({ now: () => clock });
    const s = stroke([[0.1, 0.1]], { updatedAt: 10 });
    doc.commit({ before: [], after: doc.fresh([s]) });
    const e = doc.entry(s.id);
    if (!e) throw new Error("missing");
    clock = 20;
    doc.commit({ before: [e], after: [{ item: { ...s, points: [[0.5, 0.5, 0.5, 0]], updatedAt: 20 }, seq: e.seq }] });
    clock = 30;
    const d = doc.undo();
    expect(d?.updated[0]).toMatchObject({ points: s.points, updatedAt: 30 });
  });

  it("undo/redo within the same millisecond as the delete still beat the replica's tombstone", () => {
    const clock = 1_000; // frozen: every action in one millisecond
    const doc = new InkDocument({ now: () => clock });
    let local: InkItem[] = [];
    doc.onDiff((d) => {
      local = recordInkDiff(local, d, clock);
    });
    const s = stroke([[0.1, 0.1]], { createdAt: 1_000, updatedAt: 1_000 });
    doc.commit({ before: [], after: doc.fresh([s]) });
    const e = doc.entry(s.id);
    if (!e) throw new Error("missing");
    doc.commit({ before: [e], after: [] });
    const tombstone = [...local];
    doc.undo();
    let merged = mergeInkItems(local, tombstone);
    expect(merged.find((i) => i.id === s.id)?.deleted).toBeUndefined();
    const restored = [...local];
    doc.redo();
    merged = mergeInkItems(local, restored);
    expect(merged.find((i) => i.id === s.id)?.deleted).toBe(true);
    const deletedAgain = [...local];
    doc.undo();
    merged = mergeInkItems(local, deletedAgain);
    expect(merged.find((i) => i.id === s.id)?.deleted).toBeUndefined();
  });
});
