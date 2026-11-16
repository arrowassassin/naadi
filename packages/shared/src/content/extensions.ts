/**
 * The Naadi content schema: ONE TipTap extension set shared by the seed importer (HTML -> JSON), the reader's
 * ContentRenderer and the admin editor. Contract: ledger 20261116T094551-seg05-seed-6c89be.
 *
 * Only schema-defining extensions live here. An editor adds editor-only behaviour (undo, drop cursor,
 * placeholder) on top; that never changes the stored JSON. Adding or changing a node, mark or attribute is a
 * contract change: bump CONTENT_SCHEMA_VERSION, update schema.ts and regenerate data/generated/section_bodies.json.
 */
import { type Extensions, Mark, mergeAttributes, Node } from "@tiptap/core";
import Subscript from "@tiptap/extension-subscript";
import Superscript from "@tiptap/extension-superscript";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import StarterKit from "@tiptap/starter-kit";

export const CONTENT_SCHEMA_VERSION = 1;

export const CALLOUT_VARIANTS = ["pearl", "trap", "bridge"] as const;
export type CalloutVariant = (typeof CALLOUT_VARIANTS)[number];

export const HIGHLIGHT_COLORS = ["lime", "violet", "amber"] as const;
export type HighlightColor = (typeof HIGHLIGHT_COLORS)[number];
export const HIGHLIGHT_MEANINGS = ["key_fact", "formula", "mistake"] as const;
export type HighlightMeaning = (typeof HIGHLIGHT_MEANINGS)[number];

/** Lime = key fact, violet = formula, amber = mistake (HANDOFF §4 colour roles). */
export const HIGHLIGHT_MEANING_BY_COLOR: Record<HighlightColor, HighlightMeaning> = {
  lime: "key_fact",
  violet: "formula",
  amber: "mistake",
};

function oneOf<T extends string>(values: readonly T[], v: string | null): T | null {
  return v !== null && (values as readonly string[]).includes(v) ? (v as T) : null;
}

function intOrNull(v: string | null): number | null {
  if (v === null || !/^\d{1,6}$/.test(v)) return null;
  return Number.parseInt(v, 10);
}

/** Attribute `attr` stored on the `data-*` HTML attribute `html` (parse and render symmetric). */
function dataAttr(attr: string, html: string, parse: (v: string | null) => unknown = (v) => v) {
  return {
    default: null,
    parseHTML: (el: HTMLElement) => parse(el.getAttribute(html)),
    renderHTML: (attrs: Record<string, unknown>) => {
      const value = attrs[attr];
      return value === null || value === undefined ? {} : { [html]: String(value) };
    },
  };
}

/** Pearl / trap / bridge box. Content is ordinary blocks. */
export const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    return {
      variant: {
        default: "pearl",
        parseHTML: (el: HTMLElement) =>
          CALLOUT_VARIANTS.find((v) => el.classList.contains(v)) ??
          oneOf(CALLOUT_VARIANTS, el.getAttribute("data-callout")) ??
          "pearl",
        renderHTML: (attrs: Record<string, unknown>) => ({
          class: String(attrs.variant),
          "data-callout": String(attrs.variant),
        }),
      },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-callout]" }, ...CALLOUT_VARIANTS.map((v) => ({ tag: `div.${v}`, attrs: { variant: v } }))];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", HTMLAttributes, 0];
  },
});

function atomBlock(
  name: string,
  tag: string,
  attrs: Record<string, ReturnType<typeof dataAttr>>,
  requiredDataAttr: string,
) {
  return Node.create({
    name,
    group: "block",
    atom: true,
    selectable: true,
    draggable: true,
    addAttributes() {
      return attrs;
    },
    parseHTML() {
      return [{ tag: `${tag}[${requiredDataAttr}]` }];
    },
    renderHTML({ HTMLAttributes }) {
      return [tag, mergeAttributes(HTMLAttributes)];
    },
  });
}

/** A figure: points at a `media_assets` row. Caption, alt text, attribution and URL come from that row. */
export const Figure = atomBlock("figure", "figure", { mediaId: dataAttr("mediaId", "data-media-id") }, "data-media-id");

/** A video (YouTube IFrame player or own video): a `media_assets` row of kind video_youtube / video_own. */
export const Video = atomBlock(
  "video",
  "figure",
  {
    mediaId: dataAttr("mediaId", "data-video-id"),
    startS: dataAttr("startS", "data-start-s", intOrNull),
    endS: dataAttr("endS", "data-end-s", intOrNull),
  },
  "data-video-id",
);

/** A PDF (`media_assets` kind pdf), optionally opened at a page. */
export const Pdf = atomBlock(
  "pdf",
  "figure",
  { mediaId: dataAttr("mediaId", "data-pdf-id"), page: dataAttr("page", "data-page", intOrNull) },
  "data-pdf-id",
);

/** An embedded question (stem + options only; answers are served by the API after an attempt). */
export const QuestionEmbed = atomBlock(
  "questionEmbed",
  "div",
  { questionId: dataAttr("questionId", "data-question-id") },
  "data-question-id",
);

/** A Mermaid diagram; the source lives in the attribute, never executed as HTML. */
export const Mermaid = atomBlock("mermaid", "pre", { code: dataAttr("code", "data-mermaid") }, "data-mermaid");

/** Display math (KaTeX source). */
export const MathBlock = atomBlock("math", "div", { latex: dataAttr("latex", "data-math") }, "data-math");

/** Coloured highlight with a meaning, so it is never conveyed by colour alone. */
export const Highlight = Mark.create({
  name: "highlight",
  addAttributes() {
    return {
      color: {
        default: "lime",
        parseHTML: (el: HTMLElement) => oneOf(HIGHLIGHT_COLORS, el.getAttribute("data-color")) ?? "lime",
        renderHTML: (attrs: Record<string, unknown>) => ({ "data-color": String(attrs.color) }),
      },
      meaning: {
        default: "key_fact",
        parseHTML: (el: HTMLElement) => {
          const explicit = oneOf(HIGHLIGHT_MEANINGS, el.getAttribute("data-meaning"));
          if (explicit) return explicit;
          const color = oneOf(HIGHLIGHT_COLORS, el.getAttribute("data-color")) ?? "lime";
          return HIGHLIGHT_MEANING_BY_COLOR[color];
        },
        renderHTML: (attrs: Record<string, unknown>) => ({ "data-meaning": String(attrs.meaning) }),
      },
    };
  },
  parseHTML() {
    return [{ tag: "span.hy", attrs: { color: "lime", meaning: "key_fact" } }, { tag: "mark[data-color]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["mark", HTMLAttributes, 0];
  },
});

/** Tables keep the "high-yield" styling flag of the source (`<table class="hy">`). */
export const ContentTable = Table.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      highYield: {
        default: false,
        parseHTML: (el: HTMLElement) => el.classList.contains("hy") || el.getAttribute("data-high-yield") === "true",
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.highYield ? { class: "hy", "data-high-yield": "true" } : {},
      },
    };
  },
}).configure({ resizable: false });

export const contentExtensions: Extensions = [
  StarterKit.configure({
    heading: { levels: [2, 3, 4] },
    blockquote: false,
    code: false,
    codeBlock: false,
    horizontalRule: false,
    strike: false,
    underline: false,
    link: false,
    dropcursor: false,
    gapcursor: false,
    undoRedo: false,
    listKeymap: false,
    trailingNode: false,
  }),
  Subscript,
  Superscript,
  Highlight,
  ContentTable,
  TableRow,
  TableHeader,
  TableCell,
  Callout,
  Figure,
  Video,
  Pdf,
  QuestionEmbed,
  Mermaid,
  MathBlock,
];
