import type { InkPoint } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { recognizeShape } from "./shapes";
import { sample } from "./test/helpers";

const pts = (xy: [number, number][]): InkPoint[] => xy.map(([x, y], i) => [x, y, 0.5, i * 8]);

describe("shape recognition", () => {
  it("straightens a wobbly line to its endpoints", () => {
    const r = recognizeShape(pts(sample(40, (t) => [0.1 + 0.6 * t, 0.3 + 0.1 * t], 0.004)));
    expect(r?.shape).toBe("line");
    expect(r?.points).toHaveLength(2);
  });

  it("recognises a circle and an ellipse", () => {
    const c = recognizeShape(
      pts(sample(80, (t) => [0.5 + 0.2 * Math.cos(t * Math.PI * 2), 0.5 + 0.2 * Math.sin(t * Math.PI * 2)], 0.006)),
    );
    expect(c?.shape).toBe("circle");
    const e = recognizeShape(
      pts(sample(80, (t) => [0.5 + 0.3 * Math.cos(t * Math.PI * 2), 0.5 + 0.12 * Math.sin(t * Math.PI * 2)], 0.004)),
    );
    expect(e?.shape).toBe("ellipse");
  });

  it("recognises an axis-aligned rectangle", () => {
    const corners: [number, number][] = [
      [0.2, 0.2],
      [0.6, 0.2],
      [0.6, 0.5],
      [0.2, 0.5],
      [0.2, 0.2],
    ];
    const path: [number, number][] = [];
    for (let i = 1; i < corners.length; i++) {
      const [ax, ay] = corners[i - 1] as [number, number];
      const [bx, by] = corners[i] as [number, number];
      for (let s = 0; s < 15; s++) path.push([ax + ((bx - ax) * s) / 15, ay + ((by - ay) * s) / 15]);
    }
    path.push([0.2, 0.205]);
    const r = recognizeShape(pts(path));
    expect(r?.shape).toBe("rect");
    expect(r?.points).toHaveLength(5);
  });

  it("recognises an arrow drawn as shaft then head", () => {
    const shaft = sample(30, (t) => [0.1 + 0.5 * t, 0.4], 0.002);
    const barb1 = sample(8, (t) => [0.6 - 0.06 * t, 0.4 - 0.04 * t]);
    const back = sample(8, (t) => [0.54 + 0.06 * t, 0.36 + 0.04 * t]);
    const barb2 = sample(8, (t) => [0.6 - 0.06 * t, 0.4 + 0.04 * t]);
    const r = recognizeShape(pts([...shaft, ...barb1, ...back, ...barb2]));
    expect(r?.shape).toBe("arrow");
    expect(r?.points).toHaveLength(5);
  });

  it("leaves handwriting alone", () => {
    const scribble = sample(60, (t) => [0.1 + 0.4 * t + 0.03 * Math.sin(t * 40), 0.3 + 0.05 * Math.cos(t * 23)]);
    expect(recognizeShape(pts(scribble))).toBeNull();
    expect(
      recognizeShape(
        pts([
          [0.1, 0.1],
          [0.1001, 0.1001],
          [0.1002, 0.1],
        ]),
      ),
    ).toBeNull();
  });
});
