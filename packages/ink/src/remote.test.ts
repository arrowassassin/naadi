import { type InkDiff, type InkItem, mergeInkItems, recordInkDiff } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { blockResolver } from "./anchors";
import { InkDocument } from "./document";
import { bboxOfItem, translateItem } from "./geometry";
import { stroke, textBox } from "./test/helpers";

const T = 1_700_000_000_000;

function setup(now = () => T + 1_000_000) {
  const doc = new InkDocument({ now });
  const diffs: InkDiff[] = [];
  doc.onDiff((d) => diffs.push(d));
  return { doc, diffs };
}

const line = (y: number, over: Partial<InkItem> = {}) =>
  stroke(
    [
      [0.1, y],
      [0.3, y],
    ],
    { updatedAt: T, ...over } as never,
  );

describe("InkDocument.applyRemote", () => {
  it("adds, updates and removes by LWW without undo steps or diffs", () => {
    const { doc, diffs } = setup();
    const a = line(0.1);
    const b = line(0.2);
    doc.commit({ before: [], after: doc.fresh([a, b]) });
    expect(diffs).toHaveLength(1);
    const aMoved = translateItem(a, 0, 0.05, T + 10);
    const bDead = { ...b, deleted: true as const, updatedAt: T + 10 };
    const c = line(0.4);
    const applied = doc.applyRemote([aMoved, bDead, c]);
    expect(applied).toEqual({ added: [c], updated: [aMoved], removed: [b.id] });
    expect(diffs).toHaveLength(1); // not a user action
    expect(doc.items()).toEqual([aMoved, c]);
    // Undo history is the local one, untouched: one step back is "remove the two I drew".
    expect(doc.canUndo()).toBe(true);
    doc.undo();
    expect(doc.items().map((i) => i.id)).toEqual([c.id]);
  });

  it("older or tied versions lose; a tombstone wins a tie", () => {
    const { doc } = setup();
    const a = line(0.1, { updatedAt: T + 5 });
    doc.load([a]);
    expect(doc.applyRemote([{ ...a, color: "violet", updatedAt: T + 4 }])).toBeNull();
    expect(doc.applyRemote([{ ...a, color: "violet", updatedAt: T + 5 }])).toBeNull(); // full tie: local stays
    expect(doc.items()).toEqual([a]);
    expect(doc.applyRemote([{ ...a, deleted: true, updatedAt: T + 5 }])?.removed).toEqual([a.id]);
    expect(doc.size).toBe(0);
  });

  it("never resurrects an item deleted here at or after the incoming version", () => {
    const { doc } = setup(() => T + 50);
    const a = line(0.1, { updatedAt: T + 10 });
    doc.load([a]);
    doc.commit({ before: [doc.entry(a.id) as never], after: [] }); // local delete, stamped T+50
    expect(doc.applyRemote([{ ...a, updatedAt: T + 20 }])).toBeNull();
    expect(doc.applyRemote([{ ...a, updatedAt: T + 50 }])).toBeNull(); // tie: the tombstone wins
    // A genuinely newer edit from another device does win (last writer wins).
    expect(doc.applyRemote([{ ...a, color: "lime", updatedAt: T + 60 }])?.added).toHaveLength(1);
  });

  it("undo after a remote edit re-stamps the local version so it wins everywhere", () => {
    let now = T + 100;
    const { doc, diffs } = setup(() => now);
    const a = line(0.1, { updatedAt: T + 100 });
    doc.commit({ before: [], after: doc.fresh([a]) });
    const recolor = { ...a, color: "violet" as const, updatedAt: T + 100 + 1 };
    doc.commit({ before: [doc.entry(a.id) as never], after: [{ item: recolor, seq: doc.entry(a.id)?.seq ?? 1 }] });
    // Another device edits it far in "the future" (skewed clock inside the server's window).
    const remote = { ...a, color: "amber" as const, updatedAt: T + 200_000 };
    doc.applyRemote([remote]);
    expect(doc.get(a.id)?.color).toBe("amber");
    now = T + 150; // our clock is behind theirs
    doc.undo(); // back to the first local version
    const restored = doc.get(a.id) as InkItem;
    expect(restored.color).toBe("ink");
    expect(restored.updatedAt).toBeGreaterThan(remote.updatedAt);
    // Both replicas converge on the local undo.
    const replica = recordInkDiff([remote], diffs.at(-1) as InkDiff, now);
    expect(mergeInkItems([remote], replica).find((i) => i.id === a.id)?.color).toBe("ink");
  });

  it("refuses to run inside a transaction", () => {
    const { doc } = setup();
    doc.begin();
    expect(() => doc.applyRemote([line(0.1)])).toThrow();
    doc.rollback();
  });
});

describe("InkDocument.applyRemote authoritative (the server clamped this device's stamp)", () => {
  it("adopts the server's lower stamp, so a later remote tombstone wins and the stroke stays deleted", () => {
    const ahead = T + 600_000; // this device's clock is 10 minutes fast
    const { doc, diffs } = setup(() => ahead);
    const x = line(0.1, { updatedAt: ahead });
    doc.commit({ before: [], after: doc.fresh([x]) });
    diffs.length = 0;
    const clamped = { ...x, updatedAt: T } as InkItem;
    expect(doc.applyRemote([clamped])).toBeNull(); // without the flag: our future copy wins
    expect(doc.applyRemote([clamped], { authoritative: true })).not.toBeNull();
    expect(doc.get(x.id)?.updatedAt).toBe(T);
    doc.applyRemote([{ ...clamped, deleted: true, updatedAt: T + 5 } as InkItem]); // erased on another device
    expect(doc.get(x.id)).toBeUndefined();
    expect(diffs).toEqual([]); // not a user action
  });

  it("an identical stamp is a no-op; an unknown id is added", () => {
    const { doc } = setup();
    const a = line(0.1);
    doc.load([a]);
    expect(doc.applyRemote([a], { authoritative: true })).toBeNull();
    const b = line(0.2);
    doc.applyRemote([b], { authoritative: true });
    expect(doc.get(b.id)).toEqual(b);
  });

  it("after adopting, undo re-stamps from the server's stamp, not the future one", () => {
    let now = T + 600_000;
    const { doc } = setup(() => now);
    const x = line(0.1, { updatedAt: now });
    doc.commit({ before: [], after: doc.fresh([x]) });
    doc.applyRemote([{ ...x, updatedAt: T } as InkItem], { authoritative: true });
    now = T + 10; // the corrected clock
    const e = doc.entry(x.id);
    doc.commit({ before: [e as never], after: [] });
    doc.undo();
    // One past the delete's tombstone (T + 10), and nowhere near the old future stamp.
    expect(doc.get(x.id)?.updatedAt).toBe(T + 11);
  });
});

describe("anchors", () => {
  // Three blocks; on a narrow layout they start lower (text wraps into more lines).
  const wide = blockResolver([
    { pm: 0, y: 0, fp: "heading:00000000" },
    { pm: 40, y: 0.3, fp: "paragraph:00000040" },
    { pm: 90, y: 0.6, fp: "paragraph:00000090" },
  ]);
  const narrow = blockResolver([
    { pm: 0, y: 0, fp: "heading:00000000" },
    { pm: 40, y: 0.55, fp: "paragraph:00000040" },
    { pm: 90, y: 1.2, fp: "paragraph:00000090" },
  ]);

  it("stamps the block under the item's top edge on commit", () => {
    const doc = new InkDocument({ anchors: wide });
    const a = line(0.35);
    doc.commit({ before: [], after: doc.fresh([a]) });
    const saved = doc.get(a.id) as InkItem;
    expect(saved.anchor?.pm).toBe(40);
    expect(saved.anchor?.fp).toBe("paragraph:00000040");
    expect(saved.anchor?.dy).toBeCloseTo(bboxOfItem(a).y0 - 0.3, 5);
  });

  it("places an anchored item beside the same block in another layout, and back", () => {
    const doc = new InkDocument({ anchors: wide });
    const a = line(0.35);
    doc.commit({ before: [], after: doc.fresh([a]) });
    const drawn = doc.get(a.id) as InkItem;
    const offsetInBlock = bboxOfItem(drawn).y0 - 0.3;
    doc.setAnchors(narrow);
    doc.reanchor();
    const moved = doc.get(a.id) as InkItem;
    expect(bboxOfItem(moved).y0 - 0.55).toBeCloseTo(offsetInBlock, 5);
    expect(moved.updatedAt).toBe(drawn.updatedAt); // display only: nothing to sync
    expect(moved.anchor).toEqual(drawn.anchor);
    doc.setAnchors(wide);
    doc.reanchor();
    expect(bboxOfItem(doc.get(a.id) as InkItem).y0).toBeCloseTo(bboxOfItem(drawn).y0, 5);
  });

  it("loads and merges remote items for the current layout; unanchored items stay put", () => {
    const doc = new InkDocument({ anchors: narrow });
    const anchored = { ...line(0.31), anchor: { pm: 40, dy: 0.01, fp: "paragraph:00000040" } } as InkItem;
    const plainItem = line(0.5);
    doc.load([anchored, plainItem]);
    expect(bboxOfItem(doc.get(anchored.id) as InkItem).y0).toBeCloseTo(0.56, 5);
    expect(doc.get(plainItem.id)).toEqual(plainItem);
    const remote = {
      ...textBox({ y: 0.9, updatedAt: T }),
      anchor: { pm: 90, dy: 0, fp: "paragraph:00000090" },
    } as InkItem;
    doc.applyRemote([remote]);
    expect((doc.get(remote.id) as { y: number }).y).toBeCloseTo(1.2, 5);
  });

  it("an anchor whose block is gone keeps the plain coordinates (ink is never dropped)", () => {
    const doc = new InkDocument({ anchors: wide });
    const lost = { ...line(0.42), anchor: { pm: 777, dy: 0.02 } } as InkItem;
    doc.load([lost]);
    expect(doc.get(lost.id)).toEqual(lost);
  });

  it("an edited section: an anchor whose block changed keeps its stored coordinates, never a neighbour's place", () => {
    // A paragraph was inserted at the top: pm 40 now holds the new paragraph, the anchored one moved down.
    const edited = blockResolver([
      { pm: 0, y: 0, fp: "heading:00000000" },
      { pm: 40, y: 0.3, fp: "paragraph:0000new0" },
      { pm: 75, y: 0.62, fp: "paragraph:00000040" },
    ]);
    const doc = new InkDocument({ anchors: edited });
    const beside = { ...line(0.31), anchor: { pm: 40, dy: 0.01, fp: "paragraph:00000040" } } as InkItem;
    const legacy = { ...line(0.33), anchor: { pm: 40, dy: 0.01 } } as InkItem; // no fp: never trusted
    doc.load([beside, legacy]);
    expect(doc.get(beside.id)).toEqual(beside);
    expect(doc.get(legacy.id)).toEqual(legacy);
    doc.applyRemote([{ ...beside, color: "violet", updatedAt: T + 1 } as InkItem]);
    expect(bboxOfItem(doc.get(beside.id) as InkItem).y0).toBeCloseTo(bboxOfItem(beside).y0, 6);
  });

  it("undo brings a version back placed for the current layout", () => {
    const doc = new InkDocument({ anchors: wide });
    const a = line(0.35);
    doc.commit({ before: [], after: doc.fresh([a]) });
    const e = doc.entry(a.id);
    doc.commit({ before: [e as never], after: [] });
    doc.setAnchors(narrow);
    doc.undo();
    expect(bboxOfItem(doc.get(a.id) as InkItem).y0).toBeGreaterThan(0.55);
  });
});
