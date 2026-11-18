/**
 * From a DOM selection to anchor positions (segment 13). Surfaces that take highlights render each text node
 * inside `<span data-run="<pos>">` (see `useHighlightDecorator`), so a selection boundary maps exactly to a run
 * position plus an offset, without guessing from the rendered text: labels, screen-reader words, captions and
 * buttons inside the content are simply not runs.
 */

/** Text inside these is never part of a run's text (the screen-reader word of a highlight). */
const SKIP = "[data-sr-word]";

interface RunText {
  node: Text;
  /** Position of the node's first character. */
  pos: number;
}

/** Every text node that belongs to a run, in document order, with its position. */
export function runTexts(root: Element): RunText[] {
  const out: RunText[] = [];
  for (const span of root.querySelectorAll<HTMLElement>("[data-run]")) {
    const base = Number(span.dataset.run);
    if (!Number.isInteger(base) || base < 0) continue;
    // A run nested in another run's span cannot happen (one span per text node); be safe anyway.
    if (span.parentElement?.closest("[data-run]")) continue;
    const doc = span.ownerDocument;
    const walker = doc.createTreeWalker(span, NodeFilter.SHOW_TEXT);
    let offset = 0;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text;
      if (t.parentElement?.closest(SKIP)) continue;
      out.push({ node: t, pos: base + offset });
      offset += t.data.length;
    }
  }
  return out;
}

/** -1: the point (node, offset) is before `t`'s position `i`; 0 equal; 1 after. */
function compare(doc: Document, node: Node, offset: number, t: Text, i: number): number {
  const r = doc.createRange();
  r.setStart(node, offset);
  r.collapse(true);
  // comparePoint: -1 when (t, i) is before the range, 1 when after.
  return -r.comparePoint(t, i);
}

/** The run positions a DOM range covers inside `root`, or null when it covers no run text. */
export function rangeToPositions(root: Element, range: Range): { from: number; to: number } | null {
  if (range.collapsed) return null;
  if (!root.contains(range.commonAncestorContainer)) return null;
  const texts = runTexts(root);
  if (texts.length === 0) return null;
  const doc = root.ownerDocument;
  let from: number | null = null;
  for (const t of texts) {
    if (t.node === range.startContainer) {
      if (range.startOffset < t.node.data.length) {
        from = t.pos + range.startOffset;
        break;
      }
      continue;
    }
    // The first run text that ends after the start point.
    if (compare(doc, range.startContainer, range.startOffset, t.node, t.node.data.length) < 0) {
      from = t.pos;
      break;
    }
  }
  let to: number | null = null;
  for (let k = texts.length - 1; k >= 0; k--) {
    const t = texts[k] as RunText;
    if (t.node === range.endContainer) {
      if (range.endOffset > 0) {
        to = t.pos + range.endOffset;
        break;
      }
      continue;
    }
    // The last run text that starts before the end point.
    if (compare(doc, range.endContainer, range.endOffset, t.node, 0) > 0) {
      to = t.pos + t.node.data.length;
      break;
    }
  }
  if (from === null || to === null || to <= from) return null;
  return { from, to };
}

/** The live selection's first range when it is a real (non-collapsed) selection inside `root`. */
export function selectionRangeIn(root: Element | null): Range | null {
  if (!root) return null;
  const sel = root.ownerDocument.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return null;
  return range;
}

/** True when an element takes typing: shortcuts and the toolbar's keys stay out of it. */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof Element)) return false;
  if (el.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return true;
  return el instanceof HTMLElement && el.isContentEditable;
}
