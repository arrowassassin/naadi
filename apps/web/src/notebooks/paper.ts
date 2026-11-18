/**
 * Paper templates for notebook pages (HANDOFF §5.3, segment 13): ruled, dotted, grid, Cornell and blank, as pure
 * geometry in page units (PostScript points, origin top-left, y down) on a fixed US Letter page. The same
 * primitives draw the page under the ink on screen (PaperBackground, an SVG with viewBox = the page) and in the
 * PDF export (pdf/draw.ts), so the two can never drift apart.
 *
 * Colours are ROLES, never hex: `PAPER_ROLE_TOKENS` names the design token each role is drawn with; the screen
 * resolves them through CSS custom properties and the PDF export reads the same tokens from the live stylesheet.
 */

export const PAPERS = ["ruled", "dotted", "grid", "cornell", "blank"] as const;
export type Paper = (typeof PAPERS)[number];

export function isPaper(v: unknown): v is Paper {
  return typeof v === "string" && (PAPERS as readonly string[]).includes(v);
}

/** US Letter, 8.5 × 11 in at 72 pt/in. */
export const PAGE_WIDTH = 612;
export const PAGE_HEIGHT = 792;
/** Height of the page in ink surface units (the ink surface's width is 1.0 on both axes). */
export const PAGE_HEIGHT_SU = PAGE_HEIGHT / PAGE_WIDTH;

/** What a primitive represents; each role is drawn with one token (and opacity) everywhere. */
export type PaperRole = "rule" | "margin" | "grid" | "dot" | "cue" | "summary";

export interface PaperLine {
  kind: "line";
  role: PaperRole;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  width: number;
}
export interface PaperDot {
  kind: "dot";
  role: PaperRole;
  x: number;
  y: number;
  r: number;
}
export interface PaperRect {
  kind: "rect";
  role: PaperRole;
  x: number;
  y: number;
  w: number;
  h: number;
}
export type PaperPrimitive = PaperLine | PaperDot | PaperRect;

/** The token (tokens.css) and opacity per role. The page itself is `--color-paper`. */
export const PAPER_ROLE_TOKENS: Record<PaperRole, { token: string; opacity: number }> = {
  rule: { token: "--color-text-3", opacity: 0.55 },
  margin: { token: "--color-ink-violet", opacity: 0.35 },
  grid: { token: "--color-text-3", opacity: 0.4 },
  dot: { token: "--color-paper-text-2", opacity: 0.55 },
  cue: { token: "--color-paper-text-2", opacity: 0.5 },
  summary: { token: "--color-text-2", opacity: 0.22 },
};
export const PAPER_GROUND_TOKEN = "--color-paper";

/** The geometry constants, in points. */
export const PAPER_METRICS = {
  /** Ruled line pitch: 9/32 in (college ruled). */
  rulePitch: 20.25,
  /** First rule below the header band. */
  ruleTop: 72,
  /** Bottom margin: no rule closer than this to the page edge. */
  ruleBottom: 36,
  /** Left margin line on ruled paper (1.25 in). */
  marginX: 90,
  /** Dot and grid pitch: 0.25 in. */
  gridPitch: 18,
  dotRadius: 0.9,
  ruleWidth: 0.6,
  gridWidth: 0.4,
  /** Cornell: cue column 2.5 in wide, summary band 2 in tall, header 1 in. */
  cueWidth: 180,
  summaryHeight: 144,
  headerHeight: 72,
  cueRuleWidth: 1.2,
} as const;

export interface PaperSize {
  width: number;
  height: number;
}

const LETTER: PaperSize = { width: PAGE_WIDTH, height: PAGE_HEIGHT };

/** Positions start, start+pitch, … while ≤ end (with a tolerance for float accumulation). */
function steps(start: number, end: number, pitch: number): number[] {
  const out: number[] = [];
  const n = Math.floor((end - start) / pitch + 1e-9);
  for (let i = 0; i <= n; i++) out.push(round(start + i * pitch));
  return out;
}

function round(v: number): number {
  return Math.round(v * 1000) / 1000;
}

function hline(role: PaperRole, y: number, x1: number, x2: number, width: number): PaperLine {
  return { kind: "line", role, x1, y1: y, x2, y2: y, width };
}

function vline(role: PaperRole, x: number, y1: number, y2: number, width: number): PaperLine {
  return { kind: "line", role, x1: x, y1, x2: x, y2, width };
}

/**
 * The primitives of one paper, in drawing order (fills first, then lines, then dots). Pure and deterministic:
 * the same paper and size always give the same list.
 */
export function paperPrimitives(paper: Paper, size: PaperSize = LETTER): PaperPrimitive[] {
  const { width: W, height: H } = size;
  const m = PAPER_METRICS;
  switch (paper) {
    case "blank":
      return [];
    case "ruled": {
      const out: PaperPrimitive[] = steps(m.ruleTop, H - m.ruleBottom, m.rulePitch).map((y) =>
        hline("rule", y, 0, W, m.ruleWidth),
      );
      out.push(vline("margin", m.marginX, 0, H, m.ruleWidth));
      return out;
    }
    case "grid": {
      // Centred so the margins left and right (and top and bottom) are equal.
      const x0 = round(((W % m.gridPitch) + m.gridPitch) / 2);
      const y0 = round(((H % m.gridPitch) + m.gridPitch) / 2);
      const xs = steps(x0, W - x0, m.gridPitch);
      const ys = steps(y0, H - y0, m.gridPitch);
      const top = ys[0] ?? 0;
      const bottom = ys[ys.length - 1] ?? H;
      const left = xs[0] ?? 0;
      const right = xs[xs.length - 1] ?? W;
      return [
        ...ys.map((y) => hline("grid", y, left, right, m.gridWidth)),
        ...xs.map((x) => vline("grid", x, top, bottom, m.gridWidth)),
      ];
    }
    case "dotted": {
      const x0 = round(((W % m.gridPitch) + m.gridPitch) / 2);
      const y0 = round(((H % m.gridPitch) + m.gridPitch) / 2);
      const out: PaperPrimitive[] = [];
      for (const y of steps(y0, H - y0, m.gridPitch))
        for (const x of steps(x0, W - x0, m.gridPitch)) out.push({ kind: "dot", role: "dot", x, y, r: m.dotRadius });
      return out;
    }
    case "cornell": {
      const summaryTop = H - m.summaryHeight;
      const out: PaperPrimitive[] = [
        { kind: "rect", role: "summary", x: 0, y: summaryTop, w: W, h: m.summaryHeight },
        // Header band rule, cue column, summary band edge.
        hline("cue", m.headerHeight, 0, W, m.cueRuleWidth),
        vline("cue", m.cueWidth, m.headerHeight, summaryTop, m.cueRuleWidth),
        hline("cue", summaryTop, 0, W, m.cueRuleWidth),
      ];
      // Note lines in the notes column only, between the header and the summary band.
      for (const y of steps(m.headerHeight + m.rulePitch, summaryTop - m.rulePitch / 2, m.rulePitch))
        out.push(hline("rule", y, m.cueWidth, W, m.ruleWidth));
      return out;
    }
  }
}

/** The Cornell regions in page units (for tests, the PDF and anything that wants to place content in them). */
export function cornellRegions(size: PaperSize = LETTER) {
  const m = PAPER_METRICS;
  const summaryTop = size.height - m.summaryHeight;
  return {
    header: { x: 0, y: 0, w: size.width, h: m.headerHeight },
    cue: { x: 0, y: m.headerHeight, w: m.cueWidth, h: summaryTop - m.headerHeight },
    notes: { x: m.cueWidth, y: m.headerHeight, w: size.width - m.cueWidth, h: summaryTop - m.headerHeight },
    summary: { x: 0, y: summaryTop, w: size.width, h: m.summaryHeight },
  };
}

/** An ink point in surface units → page points (the surface is one page wide). */
export function suToPage(su: number, size: PaperSize = LETTER): number {
  return su * size.width;
}

/** Fit the page into a box keeping the US Letter ratio: the CSS px size of the page. */
export function fitPage(boxWidth: number, boxHeight: number): { width: number; height: number; scale: number } {
  if (!(boxWidth > 0) || !(boxHeight > 0)) return { width: 0, height: 0, scale: 0 };
  const scale = Math.min(boxWidth / PAGE_WIDTH, boxHeight / PAGE_HEIGHT);
  return { width: PAGE_WIDTH * scale, height: PAGE_HEIGHT * scale, scale };
}
