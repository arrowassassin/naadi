import type { EmbeddedQuestion, MediaOut } from "@naadi/shared";
import type { ReactNode } from "react";

/**
 * A TipTap JSON node as stored in `chapter_sections.body` (schema owned by `@naadi/shared/content`,
 * contract 20261116T094551-seg05-seed-6c89be). The renderer walks it leniently: every attribute is checked
 * where it is used, and a node it does not know is reported instead of breaking the section.
 */
export interface ContentMark {
  type: string;
  attrs?: Record<string, unknown>;
}

export interface ContentNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: ContentNode[];
  marks?: ContentMark[];
  text?: string;
}

/** Every node type the renderer maps to a component (kept equal to the shared schema by a test). */
export const RENDERED_NODE_TYPES = [
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

export const RENDERED_MARK_TYPES = ["bold", "italic", "subscript", "superscript", "highlight"] as const;

export type RenderMode = "dev" | "prod";

export interface UnknownContent {
  kind: "node" | "mark";
  type: string;
  /** Where it was found, e.g. "ch01-s03 › block 4". */
  path: string;
}

export interface ContentEnv {
  media: Record<string, MediaOut>;
  questions: Record<string, EmbeddedQuestion>;
  mode: RenderMode;
  /** The section id, for messages and anchors. */
  sectionId: string;
  /** Level the shallowest heading in the body renders at (the section title is one above it). */
  headingBase: 2 | 3;
  /** The smallest heading level present in the body (computed once per document). */
  minHeading: number;
  /** Called once per unknown node or mark type (prod: logged; dev: also shown). */
  onUnknown?: (u: UnknownContent) => void;
  /** The origin YouTube embeds must declare (window.location.origin in the browser). */
  embedOrigin: string;
  /** Segment 20: the start/end ranges of every `video` node of the document, by media id (the chapter list). */
  videoRanges?: Record<string, { startS: number; endS: number | null }[]>;
  /** UI wording from app_config.ui_copy (shipped defaults until the config is loaded). */
  text: TextLookup;
  /** Segment 13: renders a text node at its ProseMirror position (highlights, selection anchors). */
  decorate?: TextDecorator;
  /** The ProseMirror position of every text node object of the document (only when `decorate` is set). */
  textPos?: ReadonlyMap<object, number>;
}

/** Renders the text of one text node (`pos`: its ProseMirror position, or its offset in an HTML fragment). */
export type TextDecorator = (text: string, pos: number, key: string) => ReactNode;

/** `t(key, vars)` over app_config.ui_copy, never empty (see @naadi/shared uiText and copy/useUiText.ts). */
export type TextLookup = (key: string, vars?: Readonly<Record<string, string>>) => string;

export function isNode(v: unknown): v is ContentNode {
  return typeof v === "object" && v !== null && typeof (v as { type?: unknown }).type === "string";
}

export function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

export function int(v: unknown, min: number, max: number): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= min && v <= max ? v : null;
}

export function plainText(node: ContentNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.text ?? "";
  if (node.type === "hardBreak") return " ";
  return (node.content ?? []).map(plainText).join("");
}
