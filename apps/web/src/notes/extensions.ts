import Highlight from "@tiptap/extension-highlight";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import type { AnyExtension } from "@tiptap/react";
import { ReactNodeViewRenderer } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { isHighlightColor } from "../highlights/anchor";
import { MARK_CLASS } from "../highlights/Mark";
import { NoteImageView } from "./NoteImage";

export { EMPTY_DOC, isEmptyDoc } from "./doc";

/**
 * The note sheet's schema (TipTap 3.34.0): paragraphs, headings (2–3), bullet and numbered lists, quotes, bold,
 * italic, the three highlight colours and images from the student's own uploads. Nothing renders an inline
 * `style` attribute (the production CSP has no 'unsafe-inline'): highlights carry `data-color` and token classes,
 * images resolve their signed URL in a node view. Links, code, strike and underline are left out: the reader, the
 * PDF export and search only know the marks above.
 */

/** Highlight with a fixed colour set stored as `attrs.color` ('lime' | 'violet' | 'amber'), like section content. */
export const NoteHighlight = Highlight.extend({
  addAttributes() {
    return {
      color: {
        default: "lime",
        parseHTML: (el: HTMLElement) => {
          const c = el.getAttribute("data-color");
          return isHighlightColor(c) ? c : "lime";
        },
        renderHTML: (attrs: { color?: unknown }) => {
          const c = isHighlightColor(attrs.color) ? attrs.color : "lime";
          return { "data-color": c, class: `note-mark ${MARK_CLASS[c]}` };
        },
      },
    };
  },
}).configure({ multicolor: true });

/** An image stored in the user-uploads bucket: `path` is the object key; the node view signs it to show it. */
export const NoteImage = Image.extend({
  addAttributes() {
    return {
      path: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-path"),
        renderHTML: (attrs: { path?: unknown }) => (typeof attrs.path === "string" ? { "data-path": attrs.path } : {}),
      },
      alt: { default: null },
    };
  },
  // Only our own images: a pasted <img src="https://…"> would be blocked by the CSP and is dropped instead.
  parseHTML() {
    return [{ tag: "img[data-path]" }];
  },
  addNodeView() {
    return ReactNodeViewRenderer(NoteImageView);
  },
}).configure({ inline: false, allowBase64: false });

export function noteExtensions(placeholder: string, images: boolean): AnyExtension[] {
  const list: AnyExtension[] = [
    StarterKit.configure({
      heading: { levels: [2, 3] },
      code: false,
      codeBlock: false,
      horizontalRule: false,
      strike: false,
      underline: false,
      link: false,
      // No paragraph appended by itself: opening a note must never count as an edit (and save it).
      trailingNode: false,
    }),
    NoteHighlight,
    Placeholder.configure({ placeholder }),
  ];
  if (images) list.push(NoteImage);
  return list;
}

/** A document holding `quote` as a blockquote followed by an empty paragraph to write in. */
export function quoteDoc(quote: string): Record<string, unknown> {
  return {
    type: "doc",
    content: [
      { type: "blockquote", content: [{ type: "paragraph", content: [{ type: "text", text: quote }] }] },
      { type: "paragraph" },
    ],
  };
}
