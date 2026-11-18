import type { InkStroke } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { clientToSurface, surfaceToClient } from "./coords";
import {
  bboxOfItem,
  densify,
  eraseRuns,
  itemHitByCapsule,
  pointInPolygon,
  selectedByLasso,
  translateItem,
  type Vec,
} from "./geometry";
import { GridIndex } from "./spatial";
import { stroke, textBox } from "./test/helpers";

describe("coordinate normalisation", () => {
  it("round-trips through su at every surface size and offset", () => {
    const sizes = [
      { left: 0, top: 0, width: 820, height: 1180 },
      { left: 30, top: -400, width: 1180, height: 820 },
      { left: 16, top: 120, width: 390, height: 844 },
      { left: 0, top: 0, width: 1320, height: 9000 },
    ];
    for (const frame of sizes) {
      for (const [cx, cy] of [
        [frame.left + 1, frame.top + 1],
        [frame.left + frame.width / 3, frame.top + frame.height / 2],
        [frame.left + frame.width - 1, frame.top + frame.height - 1],
      ] as [number, number][]) {
        const [x, y] = clientToSurface(frame, cx, cy);
        const [bx, by] = surfaceToClient(frame, x, y);
        expect(Math.abs(bx - cx)).toBeLessThan(0.02);
        expect(Math.abs(by - cy)).toBeLessThan(0.02);
      }
    }
  });

  it("keeps ink at the same relative place when the surface is resized (no drift > 1 px)", () => {
    const a = { left: 0, top: 0, width: 820, height: 1180 };
    const b = { left: 100, top: 50, width: 1180, height: 1180 * (1180 / 820) };
    const [x, y] = clientToSurface(a, 410, 590);
    const [px, py] = surfaceToClient(b, x, y);
    expect(Math.abs(px - (100 + 590))).toBeLessThanOrEqual(1);
    expect(Math.abs(py - (50 + 590 * (1180 / 820)))).toBeLessThanOrEqual(1);
  });

  it("uses the width for both axes (aspect ratio preserved)", () => {
    const [x, y] = clientToSurface({ left: 0, top: 0, width: 500, height: 2000 }, 250, 1000);
    expect(x).toBe(0.5);
    expect(y).toBe(2);
  });
});

describe("hit testing and erasing", () => {
  const line = stroke([
    [0.1, 0.5],
    [0.9, 0.5],
  ]);

  it("capsule hits a stroke it crosses and misses one it does not", () => {
    expect(itemHitByCapsule(line, { x: 0.5, y: 0.4 }, { x: 0.5, y: 0.6 }, 0.01)).toBe(true);
    expect(itemHitByCapsule(line, { x: 0.5, y: 0.52 }, { x: 0.5, y: 0.6 }, 0.01)).toBe(false);
    expect(itemHitByCapsule(line, { x: 0.5, y: 0.512 }, { x: 0.6, y: 0.512 }, 0.011)).toBe(true);
  });

  it("densify keeps endpoints and caps the step", () => {
    const d = densify(line.points, 0.05);
    expect(d[0]?.slice(0, 2)).toEqual([0.1, 0.5]);
    expect(d.at(-1)?.slice(0, 2)).toEqual([0.9, 0.5]);
    for (let i = 1; i < d.length; i++)
      expect(Math.abs((d[i]?.[0] ?? 0) - (d[i - 1]?.[0] ?? 0))).toBeLessThanOrEqual(0.0501);
  });

  it("pixel erase through the middle splits a stroke in two", () => {
    const runs = eraseRuns(line, [{ x: 0.5, y: 0.45 } as Vec, { x: 0.5, y: 0.55 }], 0.02);
    expect(runs).not.toBeNull();
    expect(runs?.length).toBe(2);
    const [left, right] = runs as NonNullable<typeof runs>;
    expect(Math.max(...(left ?? []).map((p) => p[0]))).toBeLessThan(0.48);
    expect(Math.min(...(right ?? []).map((p) => p[0]))).toBeGreaterThan(0.52);
  });

  it("pixel erase at the end shortens; far away does nothing; across everything deletes", () => {
    const end = eraseRuns(line, [{ x: 0.9, y: 0.5 }], 0.05);
    expect(end?.length).toBe(1);
    expect(eraseRuns(line, [{ x: 0.5, y: 0.9 }], 0.02)).toBeNull();
    const all = eraseRuns(
      line,
      [
        { x: 0.05, y: 0.5 },
        { x: 0.95, y: 0.5 },
      ],
      0.05,
    );
    expect(all).toEqual([]);
  });
});

describe("lasso", () => {
  const loop: Vec[] = [
    { x: 0.2, y: 0.2 },
    { x: 0.6, y: 0.2 },
    { x: 0.6, y: 0.6 },
    { x: 0.2, y: 0.6 },
  ];
  const box = { x0: 0.2, y0: 0.2, x1: 0.6, y1: 0.6 };

  it("point in polygon", () => {
    expect(pointInPolygon(0.4, 0.4, loop)).toBe(true);
    expect(pointInPolygon(0.7, 0.4, loop)).toBe(false);
  });

  it("selects strokes mostly inside, not ones mostly outside, and text by its centre", () => {
    const inside = stroke([
      [0.3, 0.3],
      [0.4, 0.35],
      [0.5, 0.3],
    ]);
    const poking = stroke([
      [0.3, 0.4],
      [0.45, 0.4],
      [0.55, 0.4],
      [0.65, 0.4],
    ]);
    const outside = stroke([
      [0.5, 0.5],
      [0.7, 0.7],
      [0.8, 0.8],
      [0.9, 0.9],
    ]);
    expect(selectedByLasso(inside, loop, box)).toBe(true);
    expect(selectedByLasso(poking, loop, box)).toBe(true);
    expect(selectedByLasso(outside, loop, box)).toBe(false);
    expect(selectedByLasso(textBox({ x: 0.25, y: 0.3, w: 0.2 }), loop, box)).toBe(true);
    expect(selectedByLasso(textBox({ x: 0.55, y: 0.3, w: 0.3 }), loop, box)).toBe(false);
  });

  it("translate moves points and bumps updatedAt", () => {
    const s = stroke([
      [0.1, 0.1],
      [0.2, 0.2],
    ]);
    const m = translateItem(s, 0.05, -0.05, 99) as InkStroke;
    expect(m.points[0]?.slice(0, 2)).toEqual([0.15, 0.05]);
    expect(m.updatedAt).toBe(99);
    expect(m.id).toBe(s.id);
  });
});

describe("spatial index", () => {
  it("returns exactly the items intersecting a query, across 5k random strokes", () => {
    const idx = new GridIndex();
    let seed = 7;
    const rnd = () => {
      seed = (seed * 16807) % 2147483647;
      return seed / 2147483647;
    };
    const items = Array.from({ length: 5000 }, () => {
      const x = rnd();
      const y = rnd() * 4;
      return stroke([
        [x, y],
        [x + rnd() * 0.05, y + rnd() * 0.05],
      ]);
    });
    for (const s of items) idx.insert(s.id, bboxOfItem(s));
    const q = { x0: 0.2, y0: 1.0, x1: 0.5, y1: 1.4 };
    const expected = items
      .filter((s) => {
        const b = bboxOfItem(s);
        return b.x0 <= q.x1 && q.x0 <= b.x1 && b.y0 <= q.y1 && q.y0 <= b.y1;
      })
      .map((s) => s.id)
      .sort();
    expect([...idx.query(q)].sort()).toEqual(expected);
    const gone = expected[0] as string;
    idx.remove(gone);
    expect(idx.query(q).has(gone)).toBe(false);
    expect(idx.size).toBe(4999);
  });
});
