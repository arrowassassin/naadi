/**
 * TipTap document helpers that need no TipTap: the Reader's static graph imports these (the editor itself is a
 * lazy chunk), so this module must stay free of `@tiptap/*` imports.
 */

/** An empty TipTap document. */
export const EMPTY_DOC = { type: "doc", content: [{ type: "paragraph" }] } as const;

/** True when a TipTap document holds no text and no image. */
export function isEmptyDoc(doc: unknown): boolean {
  const walk = (n: unknown): boolean => {
    if (!n || typeof n !== "object") return true;
    const node = n as { type?: unknown; text?: unknown; content?: unknown };
    if (node.type === "text") return !(typeof node.text === "string" && node.text.trim());
    if (node.type === "image") return false;
    return Array.isArray(node.content) ? node.content.every(walk) : true;
  };
  return walk(doc);
}
