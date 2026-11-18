/**
 * ProseMirror positions of a content document's top-level blocks, computed from the stored TipTap JSON without
 * loading the editor schema (browser-light: the reader imports this, not @tiptap/core).
 *
 * Rules (ProseMirror): a text node's size is its UTF-16 length; a leaf node (an atom block or hardBreak) is 1;
 * any other node is 2 + the sizes of its children. Position 0 is the start of the doc's content, so block i starts
 * at the sum of the sizes of blocks 0..i-1. positions.test.ts checks this against Node.fromJSON with the real
 * content schema. Ink on a section anchors to these positions (segment 12b), so a stroke drawn beside a paragraph
 * on one device sits beside the same paragraph after the text reflows on another.
 */

/** Leaf node types of the content schema (contentExtensions): atoms and hardBreak. */
export const CONTENT_LEAF_TYPES: ReadonlySet<string> = new Set([
  "figure",
  "video",
  "pdf",
  "questionEmbed",
  "mermaid",
  "math",
  "hardBreak",
]);

interface JsonNode {
  type?: unknown;
  text?: unknown;
  content?: unknown;
}

export function nodeSize(node: unknown): number {
  if (!node || typeof node !== "object") return 0;
  const n = node as JsonNode;
  if (n.type === "text") return typeof n.text === "string" ? n.text.length : 0;
  if (typeof n.type === "string" && CONTENT_LEAF_TYPES.has(n.type)) return 1;
  let size = 2;
  if (Array.isArray(n.content)) for (const c of n.content) size += nodeSize(c);
  return size;
}

/** Start position of each top-level block of `doc` (empty for anything that is not a doc). */
export function topLevelBlockPositions(doc: unknown): number[] {
  if (!doc || typeof doc !== "object" || (doc as JsonNode).type !== "doc") return [];
  const content = (doc as JsonNode).content;
  if (!Array.isArray(content)) return [];
  const out: number[] = [];
  let pos = 0;
  for (const block of content) {
    out.push(pos);
    pos += nodeSize(block);
  }
  return out;
}

/** fnv1a-32 over UTF-16 code units, as 8 hex digits. */
function fnv1a32(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/**
 * A top-level block's fingerprint, `<node type>:<8 hex>` (InkAnchor.fp). Any change to the block (text, marks,
 * attrs) changes it, so ink anchored to a block that was edited, removed or replaced keeps its stored
 * coordinates instead of moving beside whatever block now starts at the same position.
 */
export function blockFingerprint(block: unknown): string {
  const type = block && typeof block === "object" ? (block as JsonNode).type : undefined;
  const name = typeof type === "string" && /^[A-Za-z][A-Za-z0-9]{0,30}$/.test(type) ? type : "block";
  return `${name}:${fnv1a32(JSON.stringify(block) ?? "")}`;
}

/** Each top-level block's start position and fingerprint (empty for anything that is not a doc). */
export function topLevelBlocks(doc: unknown): { pm: number; fp: string }[] {
  const pms = topLevelBlockPositions(doc);
  const content = pms.length ? ((doc as JsonNode).content as unknown[]) : [];
  return pms.map((pm, i) => ({ pm, fp: blockFingerprint(content[i]) }));
}
