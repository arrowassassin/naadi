import { describe, expect, it } from "vitest";
import {
  cornellRegions,
  fitPage,
  isPaper,
  PAGE_HEIGHT,
  PAGE_HEIGHT_SU,
  PAGE_WIDTH,
  PAPER_METRICS,
  PAPER_ROLE_TOKENS,
  PAPERS,
  type PaperLine,
  paperPrimitives,
} from "./paper";

const lines = (p: ReturnType<typeof paperPrimitives>) => p.filter((x): x is PaperLine => x.kind === "line");

describe("paper templates", () => {
  it("are the five papers the notebooks table allows", () => {
    expect(PAPERS).toEqual(["ruled", "dotted", "grid", "cornell", "blank"]);
    expect(isPaper("grid")).toBe(true);
    expect(isPaper("graph")).toBe(false);
    expect(isPaper(null)).toBe(false);
  });

  it("is a US Letter page", () => {
    expect([PAGE_WIDTH, PAGE_HEIGHT]).toEqual([612, 792]);
    expect(PAGE_HEIGHT_SU).toBeCloseTo(11 / 8.5, 10);
  });

  it("blank has nothing to draw", () => {
    expect(paperPrimitives("blank")).toEqual([]);
  });

  it("ruled: evenly pitched full-width rules inside the margins plus one margin line", () => {
    const p = paperPrimitives("ruled");
    const rules = lines(p).filter((l) => l.role === "rule");
    expect(rules[0]?.y1).toBe(PAPER_METRICS.ruleTop);
    for (let i = 1; i < rules.length; i++)
      expect((rules[i]?.y1 ?? 0) - (rules[i - 1]?.y1 ?? 0)).toBeCloseTo(PAPER_METRICS.rulePitch, 6);
    expect(rules.at(-1)?.y1).toBeLessThanOrEqual(PAGE_HEIGHT - PAPER_METRICS.ruleBottom);
    expect(rules.every((l) => l.x1 === 0 && l.x2 === PAGE_WIDTH && l.y1 === l.y2)).toBe(true);
    expect(rules).toHaveLength(34);
    const margin = lines(p).filter((l) => l.role === "margin");
    expect(margin).toEqual([{ kind: "line", role: "margin", x1: 90, y1: 0, x2: 90, y2: PAGE_HEIGHT, width: 0.6 }]);
  });

  it("grid: square cells with equal margins", () => {
    const l = lines(paperPrimitives("grid"));
    const h = l.filter((x) => x.y1 === x.y2);
    const v = l.filter((x) => x.x1 === x.x2);
    expect(h).toHaveLength(44);
    expect(v).toHaveLength(34);
    expect(h[0]?.y1).toBe(9);
    expect(PAGE_HEIGHT - (h.at(-1)?.y1 ?? 0)).toBe(9);
    expect(v[0]?.x1).toBe(9);
    expect(PAGE_WIDTH - (v.at(-1)?.x1 ?? 0)).toBe(9);
    // Lines stop at the outer cells instead of running into the margins.
    expect(h.every((x) => x.x1 === 9 && x.x2 === 603)).toBe(true);
  });

  it("dotted: one dot per grid crossing", () => {
    const d = paperPrimitives("dotted");
    expect(d.every((x) => x.kind === "dot" && x.r === PAPER_METRICS.dotRadius)).toBe(true);
    expect(d).toHaveLength(34 * 44);
    expect(d[0]).toMatchObject({ x: 9, y: 9 });
  });

  it("cornell: cue column, header band, summary band and note lines only in the notes column", () => {
    const p = paperPrimitives("cornell");
    const r = cornellRegions();
    expect(r.cue).toEqual({ x: 0, y: 72, w: 180, h: 576 });
    expect(r.summary).toEqual({ x: 0, y: 648, w: 612, h: 144 });
    expect(p[0]).toEqual({ kind: "rect", role: "summary", x: 0, y: 648, w: 612, h: 144 });
    const cue = lines(p).filter((l) => l.role === "cue");
    expect(cue).toHaveLength(3);
    expect(cue.find((l) => l.x1 === l.x2)).toMatchObject({ x1: 180, y1: 72, y2: 648 });
    const rules = lines(p).filter((l) => l.role === "rule");
    expect(rules.length).toBeGreaterThan(20);
    expect(rules.every((l) => l.x1 === 180 && l.x2 === 612 && l.y1 > 72 && l.y1 < 648)).toBe(true);
  });

  it("is deterministic and stays inside the page for every paper", () => {
    for (const paper of PAPERS) {
      expect(paperPrimitives(paper)).toEqual(paperPrimitives(paper));
      for (const x of paperPrimitives(paper)) {
        if (x.kind === "line") {
          for (const v of [x.x1, x.x2]) expect(v).toBeGreaterThanOrEqual(0);
          for (const v of [x.x1, x.x2]) expect(v).toBeLessThanOrEqual(PAGE_WIDTH);
          for (const v of [x.y1, x.y2]) expect(v).toBeLessThanOrEqual(PAGE_HEIGHT);
        } else if (x.kind === "dot") {
          expect(x.x).toBeLessThan(PAGE_WIDTH);
          expect(x.y).toBeLessThan(PAGE_HEIGHT);
        }
        expect(PAPER_ROLE_TOKENS[x.role].token).toMatch(/^--color-/);
      }
    }
  });

  it("scales to other sizes", () => {
    const a4 = paperPrimitives("ruled", { width: 595, height: 842 });
    expect(lines(a4).find((l) => l.role === "rule")?.x2).toBe(595);
  });

  it("fits the page into a box keeping its ratio", () => {
    expect(fitPage(612, 2000)).toEqual({ width: 612, height: 792, scale: 1 });
    const f = fitPage(1000, 792);
    expect(f.scale).toBe(1);
    expect(fitPage(306, 792).height).toBe(396);
    expect(fitPage(0, 100)).toEqual({ width: 0, height: 0, scale: 0 });
  });
});
