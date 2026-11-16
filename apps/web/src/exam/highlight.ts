/**
 * Highlights on an exam stem, stored as character ranges over the stem's text content (layout- and
 * zoom-independent) and painted with the CSS Custom Highlight API (`::highlight(naadi-exam)`), so the
 * question's markup is never modified. Where the API is missing the stored ranges stay and simply do not paint.
 */
export type Span = [number, number];

export const HIGHLIGHT_NAME = "naadi-exam";

/** Merge overlapping or touching spans; drop empty ones. */
export function normalize(spans: readonly Span[]): Span[] {
  const sorted = spans.filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const out: Span[] = [];
  for (const [a, b] of sorted) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

/** Add a span; selecting inside an existing highlight removes that part instead (a toggle, as on test day). */
export function toggleSpan(spans: readonly Span[], add: Span): Span[] {
  const [a, b] = add;
  if (b <= a) return normalize(spans);
  const inside = spans.some(([x, y]) => x <= a && b <= y);
  if (!inside) return normalize([...spans, add]);
  const out: Span[] = [];
  for (const [x, y] of spans) {
    if (y <= a || x >= b) out.push([x, y]);
    else {
      if (x < a) out.push([x, a]);
      if (y > b) out.push([b, y]);
    }
  }
  return normalize(out);
}

function textNodes(root: Node): Text[] {
  const out: Text[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) out.push(n as Text);
  return out;
}

/** The text offset of (node, offset) inside `root`, or null when the point is outside it. */
export function offsetOf(root: Node, node: Node, offset: number): number | null {
  if (!root.contains(node)) return null;
  let total = 0;
  for (const t of textNodes(root)) {
    if (t === node) return total + offset;
    if (node.nodeType !== Node.TEXT_NODE && node.contains(t)) {
      // (element, childIndex) points: count text up to the child at that index
      const child = node.childNodes[offset];
      if (child && (child === t || child.contains(t))) return total;
    }
    total += t.data.length;
  }
  return node === root || root.contains(node) ? total : null;
}

/** The current selection as a span of `root`'s text, if it lies inside it and is not empty. */
export function selectionSpan(root: HTMLElement, sel: Selection | null): Span | null {
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  const a = offsetOf(root, r.startContainer, r.startOffset);
  const b = offsetOf(root, r.endContainer, r.endOffset);
  if (a === null || b === null || b <= a) return null;
  return [a, b];
}

/** DOM ranges for spans over `root`'s text. */
export function rangesFor(root: HTMLElement, spans: readonly Span[]): Range[] {
  const nodes = textNodes(root);
  const out: Range[] = [];
  for (const [a, b] of spans) {
    let pos = 0;
    let start: [Text, number] | null = null;
    let end: [Text, number] | null = null;
    for (const t of nodes) {
      const len = t.data.length;
      if (!start && a < pos + len) start = [t, a - pos];
      if (b <= pos + len) {
        end = [t, b - pos];
        break;
      }
      pos += len;
    }
    if (start && end) {
      const r = document.createRange();
      r.setStart(start[0], Math.max(0, start[1]));
      r.setEnd(end[0], Math.max(0, end[1]));
      out.push(r);
    }
  }
  return out;
}

interface HighlightRegistry {
  set(name: string, h: unknown): void;
  delete(name: string): void;
}

function registry(): { reg: HighlightRegistry; make: (...r: Range[]) => unknown } | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const H = (globalThis as { Highlight?: new (...r: Range[]) => unknown }).Highlight;
  if (!css?.highlights || !H) return null;
  return { reg: css.highlights, make: (...r) => new H(...r) };
}

export function highlightsSupported(): boolean {
  return registry() !== null;
}

/** Paint the spans (replaces what was painted before); returns false when the browser cannot paint them. */
export function paint(root: HTMLElement | null, spans: readonly Span[]): boolean {
  const r = registry();
  if (!r) return false;
  if (!root || spans.length === 0) {
    r.reg.delete(HIGHLIGHT_NAME);
    return true;
  }
  r.reg.set(HIGHLIGHT_NAME, r.make(...rangesFor(root, spans)));
  return true;
}
