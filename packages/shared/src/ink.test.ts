import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  applyInkDiff,
  type InkItem,
  InkItemSchema,
  InkItemsSchema,
  type InkStroke,
  inkItemsJsonSchema,
  isUuidV7,
  mergeInkItems,
  roundSu,
  uuidv7,
} from "./ink";

const SCHEMA_FILE = path.resolve(import.meta.dirname, "../ink.schema.json");

function stroke(over: Partial<InkStroke> = {}): InkStroke {
  return {
    id: uuidv7(),
    tool: "pen",
    color: "ink",
    size: 0.004,
    points: [
      [0.1, 0.1, 0.5, 0],
      [0.2, 0.15, 0.6, 8],
    ],
    createdAt: 1_700_000_000_000,
    updatedAt: 1_700_000_000_000,
    ...over,
  };
}

describe("uuidv7", () => {
  it("produces valid, unique, time-ordered ids", () => {
    const ids = Array.from({ length: 5000 }, () => uuidv7(1_700_000_000_000));
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(isUuidV7(id)).toBe(true);
    expect([...ids].sort()).toEqual(ids);
    const later = uuidv7(1_700_000_000_500);
    expect(later > (ids.at(-1) as string)).toBe(true);
  });

  it("encodes the timestamp in the first 48 bits", () => {
    const ms = Date.UTC(2026, 10, 16);
    const id = uuidv7(ms + 10_000);
    expect(Number.parseInt(id.replace(/-/g, "").slice(0, 12), 16)).toBeGreaterThanOrEqual(ms + 10_000);
  });

  it("rejects other uuid versions and uppercase", () => {
    expect(isUuidV7("0190a0b2-7c3d-4e5f-8a1b-2c3d4e5f6a7b")).toBe(false);
    expect(isUuidV7(uuidv7().toUpperCase())).toBe(false);
  });
});

describe("schema", () => {
  it("accepts a pen stroke, a shape and a text box", () => {
    expect(InkItemSchema.parse(stroke())).toBeTruthy();
    expect(InkItemSchema.parse(stroke({ tool: "marker", color: "lime", size: 0.02, shape: "line" }))).toBeTruthy();
    const text: InkItem = {
      id: uuidv7(),
      tool: "text",
      color: "violet",
      size: 0.02,
      x: 0.1,
      y: 0.3,
      w: 0.4,
      text: "Type 4 = aldo ↓",
      anchor: { pm: 42, dy: 0.01 },
      createdAt: 1,
      updatedAt: 2,
    };
    expect(InkItemSchema.parse(text)).toEqual(text);
  });

  it("rejects bad points, colours, extra keys and NaN", () => {
    expect(InkItemSchema.safeParse(stroke({ points: [] })).success).toBe(false);
    expect(InkItemSchema.safeParse(stroke({ points: [[0, 0, 1.5, 0]] })).success).toBe(false);
    expect(InkItemSchema.safeParse(stroke({ points: [[Number.NaN, 0, 0.5, 0]] })).success).toBe(false);
    expect(InkItemSchema.safeParse(stroke({ points: [[0, 0, 0.5, 1.5]] })).success).toBe(false);
    expect(InkItemSchema.safeParse({ ...stroke(), color: "#ff0000" }).success).toBe(false);
    expect(InkItemSchema.safeParse({ ...stroke(), extra: 1 }).success).toBe(false);
    expect(InkItemSchema.safeParse({ ...stroke(), id: "not-a-uuid" }).success).toBe(false);
  });

  it("rejects duplicate ids in a layer", () => {
    const s = stroke();
    expect(InkItemsSchema.safeParse([s, s]).success).toBe(false);
    expect(InkItemsSchema.safeParse([s, stroke()]).success).toBe(true);
  });

  it("rounds su values without negative zero", () => {
    expect(roundSu(0.123456789)).toBe(0.12346);
    expect(Object.is(roundSu(-0.000001), 0)).toBe(true);
  });

  it("ink.schema.json is up to date (UPDATE_INK_SCHEMA=1 regenerates it)", () => {
    const generated = inkItemsJsonSchema();
    if (process.env.UPDATE_INK_SCHEMA === "1") writeFileSync(SCHEMA_FILE, `${JSON.stringify(generated, null, 2)}\n`);
    expect(JSON.parse(readFileSync(SCHEMA_FILE, "utf8"))).toEqual(generated);
  });
});

describe("merge and diff", () => {
  it("merges replicas by id, last writer wins, commutative", () => {
    const a = stroke({ updatedAt: 10 });
    const aMoved = { ...a, points: [[0.3, 0.3, 0.5, 0]] as InkStroke["points"], updatedAt: 20 };
    const b = stroke();
    const c = stroke();
    const left = [a, b];
    const right = [aMoved, c];
    const m1 = mergeInkItems(left, right);
    const m2 = mergeInkItems(right, left);
    expect(m1).toEqual(m2);
    expect(m1.find((i) => i.id === a.id)).toEqual(aMoved);
    expect(m1.map((i) => i.id)).toEqual([a.id, b.id, c.id].sort());
  });

  it("a tombstone beats a concurrent edit at the same time, and is never resurrected by an older copy", () => {
    const a = stroke({ updatedAt: 10 });
    const dead = { ...a, deleted: true as const, updatedAt: 30 };
    expect(mergeInkItems([a], [dead])[0]?.deleted).toBe(true);
    const tieEdit = { ...a, color: "violet" as const, updatedAt: 30 };
    expect(mergeInkItems([tieEdit], [dead])[0]?.deleted).toBe(true);
    expect(mergeInkItems([dead], [tieEdit])[0]?.deleted).toBe(true);
  });

  it("applies a diff in order", () => {
    const a = stroke();
    const b = stroke();
    const c = stroke();
    const bNew = { ...b, color: "violet" as const, updatedAt: b.updatedAt + 1 };
    const out = applyInkDiff([a, b], { added: [c], updated: [bNew], removed: [a.id] });
    expect(out).toEqual([bNew, c]);
  });
});
