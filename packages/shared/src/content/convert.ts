/**
 * HTML -> Naadi content JSON (TipTap), for the seed importer and server-side HTML import. Node only
 * (`@tiptap/html` resolves to its happy-dom build under Node).
 *
 * The conversion is strict: generateJSON silently drops markup the schema does not know, so the input is
 * first checked against an allowlist of tags, classes and attributes, and afterwards every character of text
 * is compared with the output. Anything unexpected throws ContentConversionError naming the problem.
 */
import { getSchema } from "@tiptap/core";
import { generateJSON } from "@tiptap/html";
import { Node as PmNode } from "@tiptap/pm/model";
import { Window } from "happy-dom";
import { CALLOUT_VARIANTS, contentExtensions } from "./extensions";
import { type ContentDoc, ContentDocSchema, figureIdsOf, textOf } from "./schema";

export class ContentConversionError extends Error {
  constructor(
    message: string,
    readonly problems: string[],
  ) {
    super(
      `${message}: ${problems.slice(0, 5).join("; ")}${problems.length > 5 ? ` (+${problems.length - 5} more)` : ""}`,
    );
    this.name = "ContentConversionError";
  }
}

/** `[[figure:ID]]` placeholders in seed HTML. IDs are media_assets ids. */
const FIGURE_PLACEHOLDER = /\[\[figure:([^\]]*)\]\]/g;
const MEDIA_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/;
const WORDS_PER_MINUTE = 200;

type AttrRule = RegExp | readonly string[];

/** Allowed elements and, per element, allowed attributes (value pattern) and classes. */
const ALLOWED: Record<string, { attrs?: Record<string, AttrRule>; classes?: readonly string[] }> = {
  p: {},
  h2: {},
  h3: {},
  h4: {},
  b: {},
  strong: {},
  i: {},
  em: {},
  sub: {},
  sup: {},
  br: {},
  ul: {},
  ol: { attrs: { start: /^\d{1,4}$/ } },
  li: {},
  table: { classes: ["hy"] },
  thead: {},
  tbody: {},
  tr: {},
  th: { attrs: { colspan: /^[1-9]\d?$/, rowspan: /^[1-9]\d?$/ } },
  td: { attrs: { colspan: /^[1-9]\d?$/, rowspan: /^[1-9]\d?$/ } },
  div: { classes: CALLOUT_VARIANTS },
  span: { classes: ["hy"] },
  figure: { attrs: { "data-media-id": MEDIA_ID } },
};

export interface ConvertedSection {
  doc: ContentDoc;
  /** The input HTML with every `[[figure:ID]]` replaced by `<figure data-media-id="ID"></figure>`. */
  html: string;
  /** Figure media ids in placement order. */
  figureIds: string[];
  readingMinutes: number;
}

/** Replace figure placeholders with figure elements; returns the ids in order. */
export function rewriteFigurePlaceholders(html: string): { html: string; figureIds: string[]; problems: string[] } {
  const figureIds: string[] = [];
  const problems: string[] = [];
  const out = html.replace(FIGURE_PLACEHOLDER, (_m, rawId: string) => {
    const id = rawId.trim();
    if (!MEDIA_ID.test(id)) {
      problems.push(`invalid figure id ${JSON.stringify(rawId)}`);
      return "";
    }
    figureIds.push(id);
    return `<figure data-media-id="${id}"></figure>`;
  });
  if (out.includes("[[figure:")) problems.push("unterminated [[figure: placeholder");
  return { html: out, figureIds, problems };
}

function newWindow(): Window {
  return new Window({
    settings: {
      disableJavaScriptEvaluation: true,
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      disableIframePageLoading: true,
      disableComputedStyleRendering: true,
    },
  });
}

// One parser window per process: creating a happy-dom Window costs far more than parsing a section.
let sharedWindow: Window | null = null;

/** Allowlist check of every element; returns the text content (whitespace removed) and the figure ids. */
function inspect(html: string): { problems: string[]; text: string; figureIds: string[] } {
  sharedWindow ??= newWindow();
  const window = sharedWindow;
  {
    const doc = new window.DOMParser().parseFromString(`<!DOCTYPE html><html><body>${html}</body></html>`, "text/html");
    const problems: string[] = [];
    for (const el of Array.from(doc.body.querySelectorAll("*"))) {
      const tag = el.tagName.toLowerCase();
      const rule = ALLOWED[tag];
      if (!rule) {
        problems.push(`unknown element <${tag}>`);
        continue;
      }
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name === "class") {
          for (const cls of Array.from(el.classList)) {
            if (!rule.classes?.includes(cls)) problems.push(`unknown class "${cls}" on <${tag}>`);
          }
          if ((tag === "div" || tag === "span") && el.classList.length !== 1) {
            problems.push(`<${tag}> needs exactly one known class`);
          }
          continue;
        }
        const allowed = rule.attrs?.[name];
        const value = attr.value ?? "";
        if (!allowed) {
          problems.push(`unknown attribute ${name} on <${tag}>`);
        } else if (allowed instanceof RegExp ? !allowed.test(value) : !allowed.includes(value)) {
          problems.push(`invalid ${name}=${JSON.stringify(value)} on <${tag}>`);
        }
      }
      if ((tag === "div" || tag === "span") && !el.hasAttribute("class")) problems.push(`<${tag}> without a class`);
    }
    const figureIds = Array.from(doc.body.querySelectorAll("figure[data-media-id]")).map(
      (el) => el.getAttribute("data-media-id") ?? "",
    );
    return { problems, text: (doc.body.textContent ?? "").replace(/\s+/g, ""), figureIds };
  }
}

export function readingMinutesFor(doc: ContentDoc): number {
  const words = textOf(doc).split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.ceil(words / WORDS_PER_MINUTE));
}

const schema = getSchema(contentExtensions);

/**
 * Convert one section's HTML. `knownMediaIds`, when given, must contain every figure id (the importer passes
 * the ids of data/figures.json, so a dangling reference fails the build, not the reader).
 */
export function htmlToContentDoc(
  sourceHtml: string,
  opts: { knownMediaIds?: ReadonlySet<string> } = {},
): ConvertedSection {
  const rewritten = rewriteFigurePlaceholders(sourceHtml);
  const problems = [...rewritten.problems];
  const seen = inspect(rewritten.html);
  problems.push(...seen.problems);
  if (opts.knownMediaIds) {
    for (const id of seen.figureIds) if (!opts.knownMediaIds.has(id)) problems.push(`unknown figure ${id}`);
  }
  if (problems.length) throw new ContentConversionError("HTML is outside the content schema", problems);

  const json = generateJSON(rewritten.html, contentExtensions);
  // ProseMirror's own structural check (content expressions, attrs), then the strict zod contract.
  PmNode.fromJSON(schema, json).check();
  const parsed = ContentDocSchema.safeParse(json);
  if (!parsed.success) {
    throw new ContentConversionError(
      "converted document fails the content schema",
      parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
    );
  }
  const doc = parsed.data;

  const after = textOf(doc).replace(/\s+/g, "");
  if (after !== seen.text) {
    let i = 0;
    while (i < after.length && after[i] === seen.text[i]) i++;
    throw new ContentConversionError("text changed in conversion", [
      `first difference at character ${i}: expected ${JSON.stringify(seen.text.slice(i, i + 40))}, got ${JSON.stringify(after.slice(i, i + 40))}`,
    ]);
  }
  const figureIds = figureIdsOf(doc);
  if (JSON.stringify(figureIds) !== JSON.stringify(seen.figureIds)) {
    throw new ContentConversionError("figures changed in conversion", [
      `source ${seen.figureIds.join(",") || "none"}, document ${figureIds.join(",") || "none"}`,
    ]);
  }
  if (JSON.stringify(doc).includes("[[figure:")) {
    throw new ContentConversionError("figure placeholder left in the document", ["[[figure:"]);
  }
  return { doc, html: rewritten.html, figureIds, readingMinutes: readingMinutesFor(doc) };
}
