import { ContentTable, contentExtensions } from "@naadi/shared/content";
import { cx } from "@naadi/ui";
import type { Extensions, JSONContent } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import { useEffect, useRef } from "react";
import type { TipTapDoc } from "./protocol";

// assistant.css is loaded once by AssistantHost (main chunk); importing it here too would make this lazy chunk
// re-request main.css with a CORS link at runtime.

/**
 * A TipTap editor on the shared content schema (@naadi/shared/content), for a draft the assistant wrote and the
 * student edits (a confirm chip's Edit, a canvas document). The stored JSON is the same schema the reader
 * renders. Editor-only change: tables render without TipTap's inline `style` (the CSP refuses style attributes).
 */
const EditorTable = ContentTable.extend({
  renderHTML({ HTMLAttributes }) {
    return ["table", HTMLAttributes, ["tbody", 0]];
  },
});

const EXTENSIONS: Extensions = contentExtensions.map((e) => (e.name === "table" ? EditorTable : e));

export interface DocEditorProps {
  doc: TipTapDoc;
  onChange: (doc: TipTapDoc) => void;
  /** Accessible name of the editing area. */
  label: string;
  /** paper = the student's own text; violet = still the assistant's draft. */
  tone: "paper" | "violet";
  editable?: boolean;
  className?: string;
}

export function DocEditor({ doc, onChange, label, tone, editable = true, className }: DocEditorProps) {
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  const editor = useEditor({
    extensions: EXTENSIONS,
    content: doc as JSONContent,
    editable,
    immediatelyRender: true,
    // TipTap would add a <style> element, which the CSP refuses; its base rules are in assistant/assistant.css.
    injectCSS: false,
    editorProps: {
      attributes: {
        "aria-label": label,
        "aria-multiline": "true",
        role: "textbox",
        class: cx(
          "min-h-40 rounded-inner p-4 text-reading outline-none [&_h2]:font-display [&_h2]:font-bold [&_h3]:font-display [&_h3]:font-bold [&_li]:ml-5 [&_ol]:list-decimal [&_p+p]:mt-3 [&_ul]:list-disc",
          tone === "paper" ? "bg-paper text-ground" : "border border-violet-line bg-violet-ground text-text",
        ),
      },
    },
    onUpdate: ({ editor: e }) => changeRef.current(e.getJSON() as TipTapDoc),
  });
  useEffect(() => {
    editor?.setEditable(editable);
  }, [editor, editable]);
  return <EditorContent editor={editor} className={className} data-doc-editor={tone} />;
}
