import { describe, expect, it } from "vitest";
import { changedCount, jsonDiff, lineDiff, withContext } from "./diff";

describe("config diff", () => {
  it("is empty for equal values", () => {
    const d = jsonDiff({ a: 1, b: [1, 2] }, { a: 1, b: [1, 2] });
    expect(changedCount(d)).toBe(0);
  });

  it("shows a changed value as one removed and one added line", () => {
    const d = jsonDiff({ notes: false, ink: true }, { notes: true, ink: true });
    expect(d.filter((l) => l.op !== "equal")).toEqual([
      { op: "delete", text: '  "notes": false,' },
      { op: "insert", text: '  "notes": true,' },
    ]);
  });

  it("aligns insertions and deletions in the middle (LCS)", () => {
    const d = lineDiff("a\nb\nc\nd", "a\nx\nc\nd\ne");
    expect(d.map((l) => `${l.op[0]}${l.text}`)).toEqual(["ea", "db", "ix", "ec", "ed", "ie"]);
  });

  it("handles empty sides", () => {
    expect(lineDiff("", "a\nb").map((l) => l.op)).toEqual(["insert", "insert"]);
    expect(lineDiff("a", "").map((l) => l.op)).toEqual(["delete"]);
  });

  it("stays linear for one change in a long value", () => {
    const big = Object.fromEntries(Array.from({ length: 3000 }, (_, i) => [`k${i}`, `v${i}`]));
    const d = jsonDiff(big, { ...big, k1500: "changed" });
    expect(changedCount(d)).toBe(2);
  });

  it("collapses unchanged runs around changes", () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join("\n");
    const after = before.replace("l10", "L10");
    const rows = withContext(lineDiff(before, after), 2);
    expect(rows[0]).toEqual({ op: "skip", n: 8 });
    expect(rows.at(-1)).toEqual({ op: "skip", n: 7 });
    expect(rows.filter((r) => r.op !== "skip")).toHaveLength(6);
  });
});
