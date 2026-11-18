/**
 * The PDF layout engine (pure: no pdf-lib, no DOM). Blocks are broken into lines with a greedy word wrap
 * measured by an injected `Measurer` (fontkit metrics in the app and in tests), paginated onto US Letter pages,
 * and returned as drawing operations in page points (origin top-left, y down; `y` of a text op is its
 * baseline). pdf/draw.ts turns the operations into pdf-lib calls; this split keeps the layout unit-testable
 * (golden page counts and text runs) without rendering anything.
 */
import type { Block, HighlightColor, Run } from "./blocks";

export type FontKey = "sans" | "sansBold" | "sansItalic" | "sansBoldItalic" | "mono";

export interface Measurer {
  /** Advance width of `text` in points. */
  width(text: string, font: FontKey, size: number): number;
  /** The font has a glyph for this code point (else the text falls back to Inter). */
  covers(font: FontKey, codePoint: number): boolean;
}

/** Text colours are roles resolved from tokens at draw time. */
export type TextRole = "text" | "muted";
export type RectRole = `hl-${HighlightColor}` | "code" | "quote" | "rule";

export type Op =
  | { op: "text"; x: number; y: number; text: string; font: FontKey; size: number; color: TextRole; strike?: boolean }
  | { op: "rect"; x: number; y: number; w: number; h: number; color: RectRole }
  | { op: "image"; src: string; x: number; y: number; w: number; h: number };

export interface LaidPage {
  ops: Op[];
}

export interface LayoutCopy {
  /** Document title (first page). */
  title: string;
  /** A line under the title ("Exported 17 Nov 2026 · 12 highlights"). */
  subtitle?: string;
  /** The words of the three highlight colours (the legend; status is never colour alone). */
  highlightWords: Record<HighlightColor, string>;
  /** "Highlights:" label in front of the legend. */
  legendLabel: string;
  /** Footer per page ("page 2 of 5"). */
  footer: (page: number, total: number) => string;
}

export interface PageGeometry {
  width: number;
  height: number;
  margin: { top: number; right: number; bottom: number; left: number };
}

export const LETTER_GEOMETRY: PageGeometry = {
  width: 612,
  height: 792,
  margin: { top: 54, right: 54, bottom: 60, left: 54 },
};

/** Type scale (points). */
export const TYPE = {
  title: { size: 22, line: 28, font: "sansBold" as FontKey },
  subtitle: { size: 8.5, line: 14 },
  h1: { size: 18, line: 24, before: 14, after: 4 },
  h2: { size: 15, line: 20, before: 12, after: 3 },
  h3: { size: 12.5, line: 18, before: 10, after: 2 },
  body: { size: 11, line: 16.5, after: 6 },
  small: { size: 9, line: 13, after: 4 },
  code: { size: 9.5, line: 14, pad: 6 },
  footer: { size: 8, baseline: 26 },
  legend: { size: 8.5, line: 16, swatch: 10 },
} as const;

/** Indent per list level. */
export const LIST_INDENT = 18;
const QUOTE_INDENT = 14;

interface Seg {
  text: string;
  font: FontKey;
  size: number;
  color: TextRole;
  strike?: boolean;
  highlight?: HighlightColor;
  width: number;
}

/** A word (pieces with no whitespace between them) or a space, with its measured width. */
interface Token {
  segs: Seg[];
  width: number;
  space: boolean;
  newline?: boolean;
}

function fontOf(run: Run): FontKey {
  if (run.code) return "mono";
  if (run.bold && run.italic) return "sansBoldItalic";
  if (run.bold) return "sansBold";
  if (run.italic) return "sansItalic";
  return "sans";
}

/** The Inter face to fall back to for a glyph the chosen face lacks (JetBrains Mono has no ⁻ or ✓). */
function fallbackOf(font: FontKey): FontKey {
  return font === "mono" ? "sans" : font;
}

/** Tabs become spaces; other control characters (and lone surrogates) are dropped: they have no glyph. */
export function cleanText(text: string): string {
  const unified = text.replace(/\t/g, "    ").replace(/\r\n?/g, "\n");
  let out = "";
  for (let i = 0; i < unified.length; i++) {
    const c = unified.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = unified.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        out += unified.slice(i, i + 2);
        i++;
      }
      continue; // a lone high surrogate
    }
    if (c >= 0xdc00 && c <= 0xdfff) continue; // a lone low surrogate
    const control = (c < 0x20 && c !== 0x0a) || (c >= 0x7f && c <= 0x9f) || c === 0x200b || c === 0xfeff;
    if (!control) out += unified.charAt(i);
  }
  return out;
}

/** Split text into segments by which font covers each character (chosen face, else its Inter fallback). */
function coverSegments(text: string, font: FontKey, base: Omit<Seg, "text" | "font" | "width">, m: Measurer): Seg[] {
  const out: Seg[] = [];
  let cur = "";
  let curFont = font;
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    const f = m.covers(font, cp) ? font : fallbackOf(font);
    if (f !== curFont && cur) {
      out.push({ ...base, text: cur, font: curFont, width: m.width(cur, curFont, base.size) });
      cur = "";
    }
    curFont = f;
    cur += ch;
  }
  if (cur) out.push({ ...base, text: cur, font: curFont, width: m.width(cur, curFont, base.size) });
  return out;
}

function tokenize(runs: readonly Run[], size: number, color: TextRole, m: Measurer, bold = false): Token[] {
  const tokens: Token[] = [];
  let word: Seg[] = [];
  const endWord = () => {
    if (word.length) tokens.push({ segs: word, width: word.reduce((s, x) => s + x.width, 0), space: false });
    word = [];
  };
  for (const run of runs) {
    const font = fontOf(bold ? { ...run, bold: true } : run);
    const base = {
      size,
      color,
      ...(run.strike ? { strike: true } : {}),
      ...(run.highlight ? { highlight: run.highlight } : {}),
    };
    for (const piece of cleanText(run.text).split(/(\n|[   - 　]+)/)) {
      if (!piece) continue;
      if (piece === "\n") {
        endWord();
        tokens.push({ segs: [], width: 0, space: false, newline: true });
      } else if (/^[   - 　]+$/.test(piece)) {
        endWord();
        const segs = coverSegments(" ", font, base, m);
        tokens.push({ segs, width: segs.reduce((s, x) => s + x.width, 0), space: true });
      } else word.push(...coverSegments(piece, font, base, m));
    }
  }
  endWord();
  return tokens;
}

/** Break one over-long word into pieces no wider than `max` (by grapheme-ish code points). */
function splitWord(tok: Token, max: number, m: Measurer): Token[] {
  const out: Token[] = [];
  let segs: Seg[] = [];
  let width = 0;
  for (const seg of tok.segs) {
    let buf = "";
    for (const ch of seg.text) {
      const w = m.width(buf + ch, seg.font, seg.size);
      if (width + w > max && (buf || segs.length)) {
        if (buf) segs.push({ ...seg, text: buf, width: m.width(buf, seg.font, seg.size) });
        out.push({ segs, width: segs.reduce((s, x) => s + x.width, 0), space: false });
        segs = [];
        width = 0;
        buf = ch;
      } else buf += ch;
    }
    if (buf) {
      const s = { ...seg, text: buf, width: m.width(buf, seg.font, seg.size) };
      segs.push(s);
      width += s.width;
    }
  }
  if (segs.length) out.push({ segs, width: segs.reduce((s, x) => s + x.width, 0), space: false });
  return out;
}

export interface Line {
  segs: Seg[];
  width: number;
}

/** Greedy word wrap at `max` points. Trailing spaces are dropped; forced breaks keep empty lines. */
export function wrapTokens(tokens: readonly Token[], max: number, m: Measurer): Line[] {
  const lines: Line[] = [];
  let cur: Token[] = [];
  let width = 0;
  const flush = () => {
    while (cur.length && cur[cur.length - 1]?.space) cur.pop();
    const segs = cur.flatMap((t) => t.segs);
    lines.push({ segs, width: segs.reduce((s, x) => s + x.width, 0) });
    cur = [];
    width = 0;
  };
  for (const tok of tokens) {
    if (tok.newline) {
      flush();
      continue;
    }
    if (tok.space && cur.length === 0) continue; // no leading spaces on a wrapped line
    if (!tok.space && tok.width > max) {
      const parts = splitWord(tok, max, m);
      for (const p of parts) {
        if (width + p.width > max && cur.length) flush();
        cur.push(p);
        width += p.width;
      }
      continue;
    }
    if (!tok.space && width + tok.width > max && cur.length) flush();
    cur.push(tok);
    width += tok.width;
  }
  if (cur.length || lines.length === 0) flush();
  return lines;
}

/** Wrap plain text (ink text boxes) into strings that fit `max`. */
export function wrapPlain(text: string, font: FontKey, size: number, max: number, m: Measurer): string[] {
  const run: Run = {
    text,
    ...(font === "mono" ? { code: true } : {}),
    ...(font === "sansBold" || font === "sansBoldItalic" ? { bold: true } : {}),
    ...(font === "sansItalic" || font === "sansBoldItalic" ? { italic: true } : {}),
  };
  return wrapTokens(tokenize([run], size, "text", m), max, m).map((l) => l.segs.map((s) => s.text).join(""));
}

class Pager {
  pages: LaidPage[] = [{ ops: [] }];
  y: number;
  constructor(
    readonly g: PageGeometry,
    readonly reserveBottom: number,
  ) {
    this.y = g.margin.top;
  }
  get bottom(): number {
    return this.g.height - this.g.margin.bottom - this.reserveBottom;
  }
  get ops(): Op[] {
    return (this.pages[this.pages.length - 1] as LaidPage).ops;
  }
  get atTop(): boolean {
    return this.y <= this.g.margin.top + 0.001;
  }
  /** Make room for `h` points (a new page if needed; a block taller than a page is placed at the top anyway). */
  need(h: number): void {
    if (this.y + h > this.bottom + 0.001 && !this.atTop) this.newPage();
  }
  newPage(): void {
    this.pages.push({ ops: [] });
    this.y = this.g.margin.top;
  }
  gap(h: number): void {
    if (!this.atTop) this.y += h;
  }
}

/** Emit one line: merged text runs plus highlight rects behind them. */
function emitLine(p: Pager, line: Line, x0: number, lineH: number, size: number): void {
  const top = p.y;
  const baseline = top + (lineH + size * 0.7) / 2;
  const hlTop = top + (lineH - size * 1.3) / 2;
  let x = x0;
  let hl: { color: HighlightColor; x: number; w: number } | null = null;
  const flushHl = () => {
    if (hl && hl.w > 0)
      p.ops.push({ op: "rect", x: hl.x - 1, y: hlTop, w: hl.w + 2, h: size * 1.3, color: `hl-${hl.color}` });
    hl = null;
  };
  const texts: Op[] = [];
  let prev: (Op & { op: "text" }) | null = null;
  for (const s of line.segs) {
    if (s.highlight) {
      if (hl && hl.color === s.highlight) hl.w = x + s.width - hl.x;
      else {
        flushHl();
        hl = { color: s.highlight, x, w: s.width };
      }
    } else flushHl();
    if (
      prev &&
      prev.font === s.font &&
      prev.size === s.size &&
      prev.color === s.color &&
      Boolean(prev.strike) === Boolean(s.strike)
    ) {
      prev.text += s.text;
    } else {
      prev = {
        op: "text",
        x,
        y: baseline,
        text: s.text,
        font: s.font,
        size: s.size,
        color: s.color,
        ...(s.strike ? { strike: true } : {}),
      };
      texts.push(prev);
    }
    x += s.width;
  }
  flushHl();
  for (const t of texts) if (t.op === "text" && t.text.trim()) p.ops.push(t);
  p.y += lineH;
}

function layoutText(
  p: Pager,
  runs: readonly Run[],
  opts: {
    size: number;
    line: number;
    x: number;
    width: number;
    color: TextRole;
    bold?: boolean;
    keepWithNext?: number;
    marker?: string;
    quote?: boolean;
  },
  m: Measurer,
): void {
  const lines = wrapTokens(tokenize(runs, opts.size, opts.color, m, opts.bold), opts.width, m);
  lines.forEach((line, i) => {
    p.need(opts.line + (i === 0 ? (opts.keepWithNext ?? 0) : 0));
    if (i === 0 && opts.marker) {
      const mw = m.width(opts.marker, "sans", opts.size);
      const top = p.y;
      p.ops.push({
        op: "text",
        x: opts.x - mw - 5,
        y: top + (opts.line + opts.size * 0.7) / 2,
        text: opts.marker,
        font: "sans",
        size: opts.size,
        color: opts.color,
      });
    }
    if (opts.quote)
      p.ops.push({ op: "rect", x: opts.x - QUOTE_INDENT + 2, y: p.y, w: 2, h: opts.line, color: "quote" });
    emitLine(p, line, opts.x, opts.line, opts.size);
  });
}

function legend(p: Pager, colors: readonly HighlightColor[], copy: LayoutCopy, m: Measurer): void {
  if (!colors.length) return;
  const { size, line, swatch } = TYPE.legend;
  p.need(line);
  const top = p.y;
  const baseline = top + (line + size * 0.7) / 2;
  let x = p.g.margin.left;
  p.ops.push({ op: "text", x, y: baseline, text: copy.legendLabel, font: "mono", size, color: "muted" });
  x += m.width(copy.legendLabel, "mono", size) + 8;
  for (const c of colors) {
    p.ops.push({ op: "rect", x, y: top + (line - swatch) / 2, w: swatch, h: swatch, color: `hl-${c}` });
    x += swatch + 4;
    const word = copy.highlightWords[c];
    p.ops.push({ op: "text", x, y: baseline, text: word, font: "sans", size, color: "text" });
    x += m.width(word, "sans", size) + 12;
  }
  p.y += line;
}

export interface LayoutInput {
  blocks: readonly Block[];
  copy: LayoutCopy;
  /** Highlight colours to explain in the legend (usedHighlights(blocks) for a note). */
  legendColors: readonly HighlightColor[];
  geometry?: PageGeometry;
  measure: Measurer;
}

/** Lay out a document: title, legend, blocks, then a footer on every page. */
export function layoutDocument(input: LayoutInput): LaidPage[] {
  const g = input.geometry ?? LETTER_GEOMETRY;
  const m = input.measure;
  const p = new Pager(g, 0);
  const left = g.margin.left;
  const contentW = g.width - g.margin.left - g.margin.right;

  layoutText(
    p,
    [{ text: input.copy.title }],
    { size: TYPE.title.size, line: TYPE.title.line, x: left, width: contentW, color: "text", bold: true },
    m,
  );
  if (input.copy.subtitle) {
    layoutText(
      p,
      [{ text: input.copy.subtitle, code: true }],
      { size: TYPE.subtitle.size, line: TYPE.subtitle.line, x: left, width: contentW, color: "muted" },
      m,
    );
  }
  legend(p, input.legendColors, input.copy, m);
  p.y += 10;

  for (const b of input.blocks) {
    switch (b.type) {
      case "heading": {
        const t = b.level === 1 ? TYPE.h1 : b.level === 2 ? TYPE.h2 : TYPE.h3;
        p.gap(t.before);
        layoutText(
          p,
          b.runs,
          {
            size: t.size,
            line: t.line,
            x: left,
            width: contentW,
            color: "text",
            bold: true,
            keepWithNext: TYPE.body.line,
          },
          m,
        );
        p.y += t.after;
        break;
      }
      case "paragraph": {
        const t = b.small ? TYPE.small : TYPE.body;
        const indent = (b.indent ?? 0) * LIST_INDENT + (b.quote ? QUOTE_INDENT : 0);
        layoutText(
          p,
          b.runs,
          {
            size: t.size,
            line: t.line,
            x: left + indent,
            width: contentW - indent,
            color: b.tone === "muted" ? "muted" : "text",
            marker: b.marker,
            quote: b.quote,
          },
          m,
        );
        p.y += t.after;
        break;
      }
      case "code": {
        const t = TYPE.code;
        const x = left + t.pad;
        const width = contentW - 2 * t.pad;
        const lines = cleanText(b.text)
          .split("\n")
          .flatMap((l) => wrapTokens(tokenize([{ text: l || " ", code: true }], t.size, "text", m), width, m));
        lines.forEach((line, i) => {
          p.need(t.line + (i === 0 ? t.pad : 0));
          if (i === 0) {
            p.ops.push({ op: "rect", x: left, y: p.y, w: contentW, h: t.pad, color: "code" });
            p.y += t.pad;
          }
          p.ops.push({ op: "rect", x: left, y: p.y, w: contentW, h: t.line, color: "code" });
          emitLine(p, line, x, t.line, t.size);
          if (i === lines.length - 1) {
            p.ops.push({ op: "rect", x: left, y: p.y, w: contentW, h: t.pad, color: "code" });
            p.y += t.pad;
          }
        });
        p.y += TYPE.body.after;
        break;
      }
      case "rule":
        p.need(13);
        p.ops.push({ op: "rect", x: left, y: p.y + 6, w: contentW, h: 0.75, color: "rule" });
        p.y += 13;
        break;
      case "image": {
        if (!(b.width > 0 && b.height > 0)) {
          if (b.alt)
            layoutText(
              p,
              [{ text: b.alt, italic: true }],
              { ...TYPE.body, x: left, width: contentW, color: "muted" },
              m,
            );
          break;
        }
        const maxH = g.height - g.margin.top - g.margin.bottom;
        const scale = Math.min(1, contentW / b.width, maxH / b.height);
        const w = b.width * scale;
        const h = b.height * scale;
        p.need(h);
        p.ops.push({ op: "image", src: b.src, x: left, y: p.y, w, h });
        p.y += h + TYPE.body.after;
        break;
      }
      case "space":
        p.gap(b.height);
        break;
    }
  }

  const total = p.pages.length;
  p.pages.forEach((page, i) => {
    const text = input.copy.footer(i + 1, total);
    const w = m.width(text, "mono", TYPE.footer.size);
    page.ops.push({
      op: "text",
      x: g.width - g.margin.right - w,
      y: g.height - TYPE.footer.baseline,
      text,
      font: "mono",
      size: TYPE.footer.size,
      color: "muted",
    });
  });
  return p.pages;
}

/** The text of a page's text ops in drawing order, joined per baseline (for tests and diagnostics). */
export function pageLines(page: LaidPage): string[] {
  const rows = new Map<number, string>();
  for (const op of page.ops) {
    if (op.op !== "text") continue;
    const k = Math.round(op.y * 100);
    rows.set(k, (rows.get(k) ?? "") + op.text);
  }
  return [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([, t]) => t);
}
