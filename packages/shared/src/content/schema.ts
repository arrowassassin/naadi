/**
 * zod contract for stored content documents (`chapter_sections.body`). Mirrors contentExtensions exactly;
 * schema.test.ts fails if a node, mark or attribute here differs from the TipTap schema.
 *
 * Strict on purpose: an unknown node or mark type, or an unexpected attribute, fails `safeParse`, so the
 * renderer can show its "unknown node" card naming the type instead of dropping content silently.
 */
import { z } from "zod";
import { CALLOUT_VARIANTS, HIGHLIGHT_COLORS, HIGHLIGHT_MEANINGS } from "./extensions";

export const CONTENT_NODE_TYPES = [
  "doc",
  "paragraph",
  "heading",
  "bulletList",
  "orderedList",
  "listItem",
  "table",
  "tableRow",
  "tableHeader",
  "tableCell",
  "callout",
  "figure",
  "video",
  "pdf",
  "questionEmbed",
  "mermaid",
  "math",
  "hardBreak",
  "text",
] as const;
export type ContentNodeType = (typeof CONTENT_NODE_TYPES)[number];

export const CONTENT_MARK_TYPES = ["bold", "italic", "subscript", "superscript", "highlight"] as const;
export type ContentMarkType = (typeof CONTENT_MARK_TYPES)[number];

const id = z.string().regex(/^[a-z0-9][a-z0-9_-]{0,127}$/);
const seconds = z.number().int().min(0).nullable();

export const ContentMarkSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("bold") }),
  z.strictObject({ type: z.literal("italic") }),
  z.strictObject({ type: z.literal("subscript") }),
  z.strictObject({ type: z.literal("superscript") }),
  z.strictObject({
    type: z.literal("highlight"),
    attrs: z.strictObject({ color: z.enum(HIGHLIGHT_COLORS), meaning: z.enum(HIGHLIGHT_MEANINGS) }),
  }),
]);
export type ContentMark = z.infer<typeof ContentMarkSchema>;

export interface TextNode {
  type: "text";
  text: string;
  marks?: ContentMark[];
}
export interface HardBreakNode {
  type: "hardBreak";
  marks?: ContentMark[];
}
export type InlineNode = TextNode | HardBreakNode;

export interface ParagraphNode {
  type: "paragraph";
  content?: InlineNode[];
}
export interface HeadingNode {
  type: "heading";
  attrs: { level: 2 | 3 | 4 };
  content?: InlineNode[];
}
export interface ListItemNode {
  type: "listItem";
  content: BlockNode[];
}
export interface BulletListNode {
  type: "bulletList";
  content: ListItemNode[];
}
export interface OrderedListNode {
  type: "orderedList";
  attrs: { start: number; type: string | null };
  content: ListItemNode[];
}
export interface CellAttrs {
  colspan: number;
  rowspan: number;
  colwidth: number[] | null;
  align: "left" | "center" | "right" | null;
}
export interface TableCellNode {
  type: "tableCell" | "tableHeader";
  attrs: CellAttrs;
  content: BlockNode[];
}
export interface TableRowNode {
  type: "tableRow";
  content?: TableCellNode[];
}
export interface TableNode {
  type: "table";
  attrs: { highYield: boolean };
  content: TableRowNode[];
}
export interface CalloutNode {
  type: "callout";
  attrs: { variant: (typeof CALLOUT_VARIANTS)[number] };
  content: BlockNode[];
}
export interface FigureNode {
  type: "figure";
  attrs: { mediaId: string };
}
export interface VideoNode {
  type: "video";
  attrs: { mediaId: string; startS: number | null; endS: number | null };
}
export interface PdfNode {
  type: "pdf";
  attrs: { mediaId: string; page: number | null };
}
export interface QuestionEmbedNode {
  type: "questionEmbed";
  attrs: { questionId: string };
}
export interface MermaidNode {
  type: "mermaid";
  attrs: { code: string };
}
export interface MathNode {
  type: "math";
  attrs: { latex: string };
}
export type BlockNode =
  | ParagraphNode
  | HeadingNode
  | BulletListNode
  | OrderedListNode
  | TableNode
  | CalloutNode
  | FigureNode
  | VideoNode
  | PdfNode
  | QuestionEmbedNode
  | MermaidNode
  | MathNode;
export type ContentNode = BlockNode | InlineNode | ListItemNode | TableRowNode | TableCellNode | ContentDoc;
export interface ContentDoc {
  type: "doc";
  content: BlockNode[];
}

const marks = z.array(ContentMarkSchema).min(1).optional();

export const TextNodeSchema: z.ZodType<TextNode> = z.strictObject({
  type: z.literal("text"),
  text: z.string().min(1),
  marks,
});
export const HardBreakNodeSchema: z.ZodType<HardBreakNode> = z.strictObject({ type: z.literal("hardBreak"), marks });
const inline = z
  .array(z.union([TextNodeSchema, HardBreakNodeSchema]))
  .min(1)
  .optional();

const cellAttrs = z.strictObject({
  colspan: z.number().int().min(1),
  rowspan: z.number().int().min(1),
  colwidth: z.array(z.number().int().min(0)).nullable(),
  align: z.enum(["left", "center", "right"]).nullable(),
});

export const BlockNodeSchema: z.ZodType<BlockNode> = z.lazy(() =>
  z.discriminatedUnion("type", [
    z.strictObject({ type: z.literal("paragraph"), content: inline }),
    z.strictObject({
      type: z.literal("heading"),
      attrs: z.strictObject({ level: z.union([z.literal(2), z.literal(3), z.literal(4)]) }),
      content: inline,
    }),
    z.strictObject({ type: z.literal("bulletList"), content: z.array(ListItemNodeSchema).min(1) }),
    z.strictObject({
      type: z.literal("orderedList"),
      attrs: z.strictObject({ start: z.number().int(), type: z.string().nullable() }),
      content: z.array(ListItemNodeSchema).min(1),
    }),
    z.strictObject({
      type: z.literal("table"),
      attrs: z.strictObject({ highYield: z.boolean() }),
      content: z.array(TableRowNodeSchema).min(1),
    }),
    z.strictObject({
      type: z.literal("callout"),
      attrs: z.strictObject({ variant: z.enum(CALLOUT_VARIANTS) }),
      content: z.array(BlockNodeSchema).min(1),
    }),
    z.strictObject({ type: z.literal("figure"), attrs: z.strictObject({ mediaId: id }) }),
    z.strictObject({
      type: z.literal("video"),
      attrs: z.strictObject({ mediaId: id, startS: seconds, endS: seconds }),
    }),
    z.strictObject({
      type: z.literal("pdf"),
      attrs: z.strictObject({ mediaId: id, page: z.number().int().min(1).nullable() }),
    }),
    z.strictObject({ type: z.literal("questionEmbed"), attrs: z.strictObject({ questionId: id }) }),
    z.strictObject({ type: z.literal("mermaid"), attrs: z.strictObject({ code: z.string().min(1).max(20_000) }) }),
    z.strictObject({ type: z.literal("math"), attrs: z.strictObject({ latex: z.string().min(1).max(5_000) }) }),
  ]),
);

export const ListItemNodeSchema: z.ZodType<ListItemNode> = z.lazy(() =>
  z.strictObject({ type: z.literal("listItem"), content: z.array(BlockNodeSchema).min(1) }),
);

export const TableCellNodeSchema: z.ZodType<TableCellNode> = z.lazy(() =>
  z.strictObject({
    type: z.enum(["tableCell", "tableHeader"]),
    attrs: cellAttrs,
    content: z.array(BlockNodeSchema).min(1),
  }),
);

export const TableRowNodeSchema: z.ZodType<TableRowNode> = z.lazy(() =>
  z.strictObject({ type: z.literal("tableRow"), content: z.array(TableCellNodeSchema).min(1).optional() }),
);

export const ContentDocSchema: z.ZodType<ContentDoc> = z.strictObject({
  type: z.literal("doc"),
  content: z.array(BlockNodeSchema).min(1),
});

type AnyNode = { type: string; text?: string; content?: AnyNode[]; marks?: { type: string }[] };

/** Every node and mark type used in a document, with counts. */
export function collectTypes(doc: ContentDoc): { nodes: Map<string, number>; marks: Map<string, number> } {
  const nodes = new Map<string, number>();
  const marksSeen = new Map<string, number>();
  const walk = (n: AnyNode) => {
    nodes.set(n.type, (nodes.get(n.type) ?? 0) + 1);
    for (const m of n.marks ?? []) marksSeen.set(m.type, (marksSeen.get(m.type) ?? 0) + 1);
    for (const c of n.content ?? []) walk(c);
  };
  walk(doc as AnyNode);
  return { nodes, marks: marksSeen };
}

/** Plain text of a document, blocks separated by spaces (for word counts and search snippets). */
export function textOf(doc: ContentNode): string {
  const parts: string[] = [];
  const walk = (n: AnyNode) => {
    if (n.type === "text" && n.text) parts.push(n.text);
    else if (n.type === "hardBreak") parts.push(" ");
    for (const c of n.content ?? []) walk(c);
    if (n.content && n.type !== "text") parts.push(" ");
  };
  walk(doc as AnyNode);
  return parts.join("").replace(/\s+/g, " ").trim();
}

/** Every figure media id in document order. */
export function figureIdsOf(doc: ContentDoc): string[] {
  const ids: string[] = [];
  const walk = (n: AnyNode & { attrs?: { mediaId?: string } }) => {
    if (n.type === "figure" && n.attrs?.mediaId) ids.push(n.attrs.mediaId);
    for (const c of n.content ?? []) walk(c);
  };
  walk(doc as AnyNode);
  return ids;
}
