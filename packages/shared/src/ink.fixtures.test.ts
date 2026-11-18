/**
 * Parity fixtures shared with the API (apps/api/tests/ink): the zod schema and the pydantic mirror must accept and
 * reject the same items, and mergeInkItems must give the golden result for every concurrent-edit vector in either
 * order (the API's SQL upsert is checked against the same file).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { type InkItem, InkItemSchema, mergeInkItems } from "./ink";

const DIR = path.resolve(import.meta.dirname, "../fixtures/ink");

interface ItemCase {
  name: string;
  valid: boolean;
  item: unknown;
}
interface MergeCase {
  name: string;
  a: InkItem[];
  b: InkItem[];
  expected: InkItem[];
}

function expand(item: unknown): unknown {
  if (item && typeof item === "object" && !Array.isArray(item)) {
    const o = item as Record<string, unknown>;
    const m = typeof o.points === "string" ? /^\$points:(\d+)$/.exec(o.points) : null;
    if (m) return { ...o, points: Array.from({ length: Number(m[1]) }, (_, i) => [0.1, 0.1, 0.5, i]) };
  }
  return item;
}

const items = JSON.parse(readFileSync(path.join(DIR, "items.json"), "utf8")) as { cases: ItemCase[] };
const merges = JSON.parse(readFileSync(path.join(DIR, "merge-vectors.json"), "utf8")) as { cases: MergeCase[] };

describe("InkItem fixtures (pydantic parity)", () => {
  it("has both valid and invalid cases", () => {
    expect(items.cases.filter((c) => c.valid).length).toBeGreaterThan(10);
    expect(items.cases.filter((c) => !c.valid).length).toBeGreaterThan(30);
  });
  for (const c of items.cases) {
    it(`${c.valid ? "accepts" : "rejects"}: ${c.name}`, () => {
      expect(InkItemSchema.safeParse(expand(c.item)).success).toBe(c.valid);
    });
  }
});

describe("merge golden vectors (SQL parity)", () => {
  for (const c of merges.cases) {
    it(c.name, () => {
      expect(mergeInkItems(c.a, c.b)).toEqual(c.expected);
      expect(mergeInkItems(c.b, c.a)).toEqual(c.expected);
      for (const i of c.expected) expect(InkItemSchema.safeParse(i).success).toBe(true);
    });
  }
});
