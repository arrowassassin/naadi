import type { InkDiff } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { InkDocument } from "./document";
import { stroke } from "./test/helpers";

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
    expect(doc.items()).toEqual([a]);
    expect(diffs).toEqual([
      { added: [a], updated: [], removed: [] },
      { added: [], updated: [], removed: [a.id] },
      { added: [a], updated: [], removed: [] },
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
    expect(doc.get(a.id)).toEqual(a);
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
    expect(doc.items()).toEqual([a]);
    doc.redo();
    expect(doc.items()).toEqual([f1]);
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
