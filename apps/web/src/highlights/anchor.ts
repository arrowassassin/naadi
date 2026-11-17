import { CONTENT_LEAF_TYPES } from "@naadi/shared/content/positions";

/**
 * Highlight anchors (segment 13): where a highlight sits in a text, kept so it survives edits to that text.
 *
 * Coordinates. A surface's text is a list of runs (`TextRun`: a start position and the run's text):
 * - a Reader section: one run per TipTap text node, `pos` its ProseMirror position in the section body (the rules of
 *   `@naadi/shared/content/positions`: a text node's size is its UTF-16 length, a leaf node is 1, any other node is
 *   2 + its children). A highlight's `from`/`to` are ProseMirror positions, as the editor would give them.
 * - a question explanation (sanitized HTML): one run per text node in document order, `pos` the UTF-16 offset of
 *   the run in the concatenated text (`htmlTextRuns` in content/SafeHtml.tsx walks exactly like the renderer).
 *
 * The anchor also stores the selected text (`quote`) with up to CONTEXT characters on each side (`prefix`,
 * `suffix`) and a fingerprint of the whole text (`revision`). When the text changes, `relocate` finds the quote
 * again (closest to where it was, preferring the occurrence whose context still matches); when it cannot be found
 * the highlight is "detached": still listed in Notes with its quote, never dropped.
 */

export interface AnchorPos {
  from: number;
  to: number;
  quote: string;
  prefix: string;
  suffix: string;
  revision: string | null;
}

export interface TextRun {
  pos: number;
  text: string;
}

/** Characters of context kept on each side of a quote. */
export const CONTEXT = 32;
/** Longest quote kept (a selection beyond this is refused by the toolbar). */
export const MAX_QUOTE = 2000;

interface JsonNode {
  type?: unknown;
  text?: unknown;
  content?: unknown;
}

/** Visit every text node object of a TipTap document with its ProseMirror position (document order). */
function walkText(doc: unknown, visit: (node: object, text: string, pos: number) => void): void {
  if (!doc || typeof doc !== "object" || (doc as JsonNode).type !== "doc") return;
  // `start` is the position where a `content` array begins (a node's content starts one after the node).
  const walk = (content: unknown, start: number): number => {
    let pos = start;
    if (!Array.isArray(content)) return pos;
    for (const child of content) {
      if (!child || typeof child !== "object") continue;
      const n = child as JsonNode;
      if (n.type === "text") {
        const text = typeof n.text === "string" ? n.text : "";
        if (text) visit(n, text, pos);
        pos += text.length;
      } else if (typeof n.type === "string" && CONTENT_LEAF_TYPES.has(n.type)) {
        pos += 1;
      } else {
        pos = walk(n.content, pos + 1) + 1;
      }
    }
    return pos;
  };
  walk((doc as JsonNode).content, 0);
}

/** The text runs of a TipTap document, with ProseMirror positions. */
export function sectionRuns(doc: unknown): TextRun[] {
  const out: TextRun[] = [];
  walkText(doc, (_node, text, pos) => out.push({ pos, text }));
  return out;
}

/** The ProseMirror position of each text node object (ContentRenderer looks its text nodes up by identity). */
export function sectionTextPositions(doc: unknown): Map<object, number> {
  const out = new Map<object, number>();
  walkText(doc, (node, _text, pos) => out.set(node, pos));
  return out;
}

/**
 * The runs joined into one string. Runs that are not contiguous (a block or line boundary between them) are joined
 * by "\n", so a quote never fuses words across paragraphs. `posOf`/`indexOf` convert between string indices and
 * run positions.
 */
export interface FlatText {
  text: string;
  /** Position of the character at string index `i`, or of the end of the previous run for a joiner. */
  posOf(i: number): number;
  /** String index of position `pos`, or -1 when `pos` is not inside (or at the end of) a run. */
  indexOf(pos: number): number;
}

export function flatten(runs: readonly TextRun[]): FlatText {
  const starts: number[] = [];
  let text = "";
  let prevEnd = -1;
  for (const r of runs) {
    if (prevEnd >= 0 && r.pos !== prevEnd) text += "\n";
    starts.push(text.length);
    text += r.text;
    prevEnd = r.pos + r.text.length;
  }
  const runAtIndex = (i: number): number => {
    let lo = 0;
    let hi = runs.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if ((starts[mid] ?? 0) <= i) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return found;
  };
  return {
    text,
    posOf(i) {
      const k = runAtIndex(i);
      if (k < 0) return runs[0]?.pos ?? 0;
      const r = runs[k] as TextRun;
      const off = i - (starts[k] as number);
      return r.pos + Math.min(off, r.text.length);
    },
    indexOf(pos) {
      // Runs are in ascending position order.
      let lo = 0;
      let hi = runs.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const r = runs[mid] as TextRun;
        if (pos < r.pos) hi = mid - 1;
        else if (pos > r.pos + r.text.length) lo = mid + 1;
        else return (starts[mid] as number) + (pos - r.pos);
      }
      return -1;
    },
  };
}

/** A short, stable fingerprint of a text (FNV-1a, 32 bit, hex). */
export function fingerprint(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `t${text.length.toString(36)}-${h.toString(16).padStart(8, "0")}`;
}

/** The anchor for positions `from`..`to` of a text, or null when the range holds no text. */
export function makeAnchor(runs: readonly TextRun[], from: number, to: number): AnchorPos | null {
  const flat = flatten(runs);
  let a = flat.indexOf(Math.min(from, to));
  let b = flat.indexOf(Math.max(from, to));
  if (a < 0 || b < 0 || b <= a) return null;
  // Trim whitespace (and block joiners) at both ends, so the quote starts and ends inside a run.
  while (a < b && /\s/.test(flat.text[a] as string)) a++;
  while (b > a && /\s/.test(flat.text[b - 1] as string)) b--;
  const quote = flat.text.slice(a, b);
  if (!quote || quote.length > MAX_QUOTE) return null;
  return {
    from: flat.posOf(a),
    to: flat.posOf(b - 1) + 1,
    quote,
    prefix: flat.text.slice(Math.max(0, a - CONTEXT), a),
    suffix: flat.text.slice(b, b + CONTEXT),
    revision: fingerprint(flat.text),
  };
}

export type Located =
  | { status: "exact" | "moved"; from: number; to: number }
  | { status: "detached"; from: null; to: null };

const DETACHED: Located = { status: "detached", from: null, to: null };

function commonSuffix(a: string, b: string): number {
  let n = 0;
  while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++;
  return n;
}

function commonPrefix(a: string, b: string): number {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

/** Collapse whitespace runs to one space, keeping a map back to the original indices. */
function normalize(s: string): { text: string; map: number[] } {
  let text = "";
  const map: number[] = [];
  let space = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i] as string;
    if (/\s/.test(c)) {
      if (!space) {
        text += " ";
        map.push(i);
      }
      space = true;
    } else {
      text += c;
      map.push(i);
      space = false;
    }
  }
  map.push(s.length);
  return { text, map };
}

function occurrences(hay: string, needle: string): number[] {
  const out: number[] = [];
  if (!needle) return out;
  let i = hay.indexOf(needle);
  while (i >= 0 && out.length < 500) {
    out.push(i);
    i = hay.indexOf(needle, i + 1);
  }
  return out;
}

/**
 * Where a stored anchor is in the current text.
 * 1. The text at `from`..`to` is still the quote: "exact" (the common case, nothing changed there).
 * 2. Otherwise every occurrence of the quote (then of the quote with whitespace collapsed) is scored by how much of
 *    the stored prefix/suffix still surrounds it, then by closeness to the old position: "moved".
 * 3. No occurrence: "detached".
 */
export function relocate(anchor: AnchorPos, runs: readonly TextRun[]): Located {
  if (!anchor.quote) return DETACHED;
  const flat = flatten(runs);
  const a = flat.indexOf(anchor.from);
  const b = flat.indexOf(anchor.to);
  if (a >= 0 && b > a && flat.text.slice(a, b) === anchor.quote) {
    return { status: "exact", from: anchor.from, to: anchor.to };
  }
  const oldIndex = a >= 0 ? a : flat.indexOf(Math.min(anchor.from, flat.posOf(flat.text.length)));

  const pick = (hay: string, quote: string, prefix: string, suffix: string, toOrig: (i: number) => number) => {
    let best: { start: number; score: number; dist: number } | null = null;
    for (const start of occurrences(hay, quote)) {
      const score =
        commonSuffix(hay.slice(Math.max(0, start - prefix.length), start), prefix) +
        commonPrefix(hay.slice(start + quote.length, start + quote.length + suffix.length), suffix);
      const dist = Math.abs(toOrig(start) - (oldIndex >= 0 ? oldIndex : toOrig(start)));
      if (!best || score > best.score || (score === best.score && dist < best.dist)) best = { start, score, dist };
    }
    return best;
  };

  const exact = pick(flat.text, anchor.quote, anchor.prefix, anchor.suffix, (i) => i);
  if (exact) {
    return {
      status: "moved",
      from: flat.posOf(exact.start),
      to: flat.posOf(exact.start + anchor.quote.length - 1) + 1,
    };
  }
  const hay = normalize(flat.text);
  const q = normalize(anchor.quote).text.trim();
  if (!q) return DETACHED;
  const loose = pick(
    hay.text,
    q,
    normalize(anchor.prefix).text,
    normalize(anchor.suffix).text,
    (i) => hay.map[i] as number,
  );
  if (!loose) return DETACHED;
  const start = hay.map[loose.start] as number;
  const end = (hay.map[loose.start + q.length - 1] as number) + 1;
  return { status: "moved", from: flat.posOf(start), to: flat.posOf(end - 1) + 1 };
}

/** A located highlight to paint: positions plus what the mark shows. */
export interface PaintRange {
  id: string;
  from: number;
  to: number;
  color: HighlightColor;
}

export type HighlightColor = "lime" | "violet" | "amber";
export const HIGHLIGHT_COLORS: readonly HighlightColor[] = ["lime", "violet", "amber"];

export function isHighlightColor(v: unknown): v is HighlightColor {
  return v === "lime" || v === "violet" || v === "amber";
}

export interface Piece {
  text: string;
  /** The highlight painted on this piece (the latest one wins where two overlap), or null. */
  range: PaintRange | null;
  /** This piece starts its highlight (the screen-reader word goes here, once per highlight). */
  first: boolean;
}

/** Split one run (`text` at `pos`) into pieces by the ranges that cover it. Ranges later in the list win. */
export function splitRun(text: string, pos: number, ranges: readonly PaintRange[]): Piece[] {
  const end = pos + text.length;
  const hits = ranges.filter((r) => r.to > pos && r.from < end);
  if (hits.length === 0) return [{ text, range: null, first: false }];
  const cuts = new Set<number>([pos, end]);
  for (const r of hits) {
    cuts.add(Math.max(pos, r.from));
    cuts.add(Math.min(end, r.to));
  }
  const points = [...cuts].sort((x, y) => x - y);
  const out: Piece[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const s = points[i] as number;
    const e = points[i + 1] as number;
    if (e <= s) continue;
    let range: PaintRange | null = null;
    for (const r of hits) if (r.from <= s && r.to >= e) range = r;
    const piece = { text: text.slice(s - pos, e - pos), range, first: range !== null && range.from === s };
    const last = out[out.length - 1];
    if (last && last.range === piece.range && !piece.first) last.text += piece.text;
    else out.push(piece);
  }
  return out;
}
