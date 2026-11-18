/**
 * TipTap JSON → a flat list of layout blocks (pure). Only the structure the PDF needs survives: paragraphs,
 * headings, bullet/ordered lists (nested), blockquotes, code blocks, rules, images and the inline marks bold,
 * italic, code, strike and the three highlight colours. Anything unknown is read for its text, so nothing the
 * student wrote is silently dropped.
 */

export type HighlightColor = "lime" | "violet" | "amber";
export const HIGHLIGHT_COLORS: readonly HighlightColor[] = ["lime", "violet", "amber"];

export interface Run {
  text: string;
  bold?: boolean;
  italic?: boolean;
  code?: boolean;
  strike?: boolean;
  highlight?: HighlightColor;
}

export type Block =
  | { type: "heading"; level: 1 | 2 | 3; runs: Run[] }
  | {
      type: "paragraph";
      runs: Run[];
      /** Left indent in list levels (each level is LIST_INDENT pt). */
      indent?: number;
      /** List marker drawn in the indent ("•", "3."). */
      marker?: string;
      /** A quoted paragraph (bar on the left). */
      quote?: boolean;
      /** Secondary text (source lines under a highlight). */
      tone?: "muted";
      small?: boolean;
    }
  | { type: "code"; text: string }
  | { type: "rule" }
  /** `src` is resolved by the caller (bytes + size) before layout; one that failed becomes its alt text. */
  | { type: "image"; src: string; alt: string; width: number; height: number }
  | { type: "space"; height: number };

interface PmMark {
  type?: string;
  attrs?: Record<string, unknown> | null;
}
export interface PmNode {
  type?: string;
  text?: string;
  attrs?: Record<string, unknown> | null;
  marks?: PmMark[] | null;
  content?: PmNode[] | null;
}

/**
 * The highlight colour of a TipTap highlight mark, whichever way the editor stores it: the colour role
 * (`lime`), the meaning (`key-fact`, `formula`, `mistake`), or a CSS custom property naming the token.
 */
export function highlightColorOf(attrs: Record<string, unknown> | null | undefined): HighlightColor {
  const raw = [attrs?.color, attrs?.kind, attrs?.["data-color"]].find((v) => typeof v === "string") as
    | string
    | undefined;
  const v = (raw ?? "").toLowerCase();
  if (v.includes("violet") || v.includes("formula")) return "violet";
  if (v.includes("amber") || v.includes("mistake")) return "amber";
  return "lime";
}

/** Plain text of a node (for unknown nodes and code blocks). */
export function nodeText(node: PmNode | null | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.text ?? "";
  if (node.type === "hardBreak") return "\n";
  return (node.content ?? []).map(nodeText).join("");
}

function inlineRuns(nodes: readonly PmNode[] | null | undefined): Run[] {
  const out: Run[] = [];
  for (const n of nodes ?? []) {
    if (n.type === "hardBreak") {
      out.push({ text: "\n" });
      continue;
    }
    if (n.type === "image") {
      const alt = typeof n.attrs?.alt === "string" ? n.attrs.alt : "";
      if (alt) out.push({ text: alt, italic: true });
      continue;
    }
    if (n.type !== "text") {
      out.push(...inlineRuns(n.content));
      continue;
    }
    const text = n.text ?? "";
    if (!text) continue;
    const run: Run = { text };
    for (const m of n.marks ?? []) {
      if (m.type === "bold" || m.type === "strong") run.bold = true;
      else if (m.type === "italic" || m.type === "em") run.italic = true;
      else if (m.type === "code") run.code = true;
      else if (m.type === "strike") run.strike = true;
      else if (m.type === "highlight") run.highlight = highlightColorOf(m.attrs);
    }
    out.push(run);
  }
  return out;
}

function headingLevel(attrs: Record<string, unknown> | null | undefined): 1 | 2 | 3 {
  const l = Number(attrs?.level);
  return l <= 1 ? 1 : l === 2 ? 2 : 3;
}

function walk(nodes: readonly PmNode[] | null | undefined, depth: number, quote: boolean, out: Block[]): void {
  for (const n of nodes ?? []) {
    switch (n.type) {
      case "paragraph": {
        const runs = inlineRuns(n.content);
        out.push({ type: "paragraph", runs, ...(depth ? { indent: depth } : {}), ...(quote ? { quote } : {}) });
        break;
      }
      case "heading":
        out.push({ type: "heading", level: headingLevel(n.attrs), runs: inlineRuns(n.content) });
        break;
      case "bulletList":
      case "orderedList":
      case "taskList": {
        const start = Number(n.attrs?.start ?? 1);
        let i = Number.isFinite(start) ? start : 1;
        for (const item of n.content ?? []) {
          const marker = n.type === "orderedList" ? `${i}.` : n.type === "taskList" && item.attrs?.checked ? "☑" : "•";
          i += 1;
          const before = out.length;
          walk(item.content, depth + 1, quote, out);
          // The marker goes on the item's first paragraph; a list item that starts with something else gets an
          // empty marked paragraph so the number is still shown.
          const first = out[before];
          if (first && first.type === "paragraph" && first.indent === depth + 1 && !first.marker) first.marker = marker;
          else out.splice(before, 0, { type: "paragraph", runs: [], indent: depth + 1, marker });
        }
        break;
      }
      case "blockquote":
        walk(n.content, depth, true, out);
        break;
      case "codeBlock":
        out.push({ type: "code", text: nodeText(n) });
        break;
      case "horizontalRule":
        out.push({ type: "rule" });
        break;
      case "image": {
        // Notes keep the storage object key in `path` (user-uploads); other content may carry a URL in `src`.
        const raw = n.attrs?.path ?? n.attrs?.src;
        const src = typeof raw === "string" ? raw : "";
        const alt = typeof n.attrs?.alt === "string" ? n.attrs.alt : "";
        if (src) out.push({ type: "image", src, alt, width: 0, height: 0 });
        else if (alt) out.push({ type: "paragraph", runs: [{ text: alt, italic: true }] });
        break;
      }
      case "doc":
        walk(n.content, depth, quote, out);
        break;
      default: {
        // Unknown block (a future extension): keep its text.
        const text = nodeText(n);
        if (text.trim()) out.push({ type: "paragraph", runs: [{ text }], ...(depth ? { indent: depth } : {}) });
      }
    }
  }
}

/** TipTap document (or a node list) → blocks. Invalid input gives no blocks. */
export function tiptapToBlocks(doc: unknown): Block[] {
  if (!doc || typeof doc !== "object") return [];
  const out: Block[] = [];
  const node = doc as PmNode;
  walk(node.type === "doc" ? node.content : [node], 0, false, out);
  return out;
}

/** The highlight colours used anywhere in the blocks, in the fixed lime/violet/amber order. */
export function usedHighlights(blocks: readonly Block[]): HighlightColor[] {
  const seen = new Set<HighlightColor>();
  for (const b of blocks)
    if (b.type === "paragraph" || b.type === "heading") for (const r of b.runs) if (r.highlight) seen.add(r.highlight);
  return HIGHLIGHT_COLORS.filter((c) => seen.has(c));
}
