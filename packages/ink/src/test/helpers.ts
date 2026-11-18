import { type InkPoint, type InkStroke, type InkTextBox, uuidv7 } from "@naadi/shared";

export function stroke(points: [number, number][], over: Partial<InkStroke> = {}): InkStroke {
  return {
    id: uuidv7(),
    tool: "pen",
    color: "ink",
    size: 0.004,
    points: points.map(([x, y], i) => [x, y, 0.5, i * 8] as InkPoint),
    createdAt: 1_700_000_000_000,
    updatedAt: 1_700_000_000_000,
    ...over,
  };
}

export function textBox(over: Partial<InkTextBox> = {}): InkTextBox {
  return {
    id: uuidv7(),
    tool: "text",
    color: "ink",
    size: 0.02,
    x: 0.1,
    y: 0.1,
    w: 0.3,
    text: "hello",
    createdAt: 1,
    updatedAt: 1,
    ...over,
  };
}

/** Points along a parametric curve. */
export function sample(n: number, f: (t: number) => [number, number], jitter = 0, seed = 1): [number, number][] {
  let s = seed;
  const rand = () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647 - 0.5;
  };
  return Array.from({ length: n }, (_, i) => {
    const [x, y] = f(i / (n - 1));
    return [x + rand() * jitter, y + rand() * jitter];
  });
}
