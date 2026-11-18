import { contentExtensions } from "@naadi/shared/content";
import { type AnyExtension, type Editor, type Extensions, ReactNodeViewRenderer } from "@tiptap/react";
import { AtomNodeView } from "./AtomNodeView";

/**
 * The admin editor's extensions: exactly the shared content schema (`contentExtensions`, the one the seed
 * converter and <ContentRenderer> use), plus editor-only behaviour that never changes the stored JSON:
 * - undo/redo, drop and gap cursors;
 * - React node views for the atom blocks (figure, video, pdf, question, mermaid, math), shown as editable cards;
 * - CSP-safe DOM for tables: TipTap renders `style="min-width…"` on <table>/<col> and `style="text-align…"` on
 *   aligned cells, which the production CSP (no 'unsafe-inline') refuses. In the editor the table renders without
 *   a colgroup and cells carry `data-align` (styled in admin.css). Node names, attributes and the JSON are inherited.
 */
export const ATOM_NODE_TYPES = ["figure", "video", "pdf", "questionEmbed", "mermaid", "math"] as const;
export type AtomNodeType = (typeof ATOM_NODE_TYPES)[number];

const ATOMS = new Set<string>(ATOM_NODE_TYPES);
const CELLS = new Set(["tableCell", "tableHeader"]);

type AttrSpec = { renderHTML?: (attrs: Record<string, unknown>) => Record<string, unknown> } & Record<string, unknown>;

function withEditorDom(ext: AnyExtension): AnyExtension {
  if (ext.name === "table") {
    return ext
      .extend({
        renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
          const { style: _style, ...rest } = HTMLAttributes;
          return ["table", rest, ["tbody", 0]];
        },
        // No TableView: it writes widths into style attributes; without it the table renders from renderHTML above.
      })
      .configure({ View: null });
  }
  if (CELLS.has(ext.name)) {
    return ext.extend({
      addAttributes() {
        const parent = ((this as unknown as { parent?: () => unknown }).parent?.() ?? {}) as Record<string, AttrSpec>;
        return {
          ...parent,
          align: {
            ...parent.align,
            renderHTML: (attrs: Record<string, unknown>) =>
              attrs.align === "left" || attrs.align === "center" || attrs.align === "right"
                ? { "data-align": attrs.align }
                : {},
          },
        };
      },
    });
  }
  return ext;
}

export function editorExtensions(opts: { nodeViews?: boolean } = {}): Extensions {
  const views = opts.nodeViews !== false;
  return contentExtensions.map((ext: AnyExtension) => {
    if (ext.name === "starterKit") {
      // Merges into the shared configuration (same nodes and marks); only editor behaviour is switched on.
      return ext.configure({ undoRedo: { depth: 200 }, dropcursor: { class: "admin-dropcursor" }, gapcursor: {} });
    }
    if (views && ATOMS.has(ext.name)) {
      return ext.extend({
        addNodeView() {
          return ReactNodeViewRenderer(AtomNodeView);
        },
      });
    }
    return withEditorDom(ext);
  });
}

/**
 * Insert a block at the cursor. When a block is selected (the figure or video just inserted is), the new block goes
 * after it: TipTap's insertContent would replace the selected node.
 */
export function insertBlock(editor: Editor, content: { type: string; attrs?: Record<string, unknown> }): void {
  const sel = editor.state.selection;
  if ("node" in sel && sel.node) editor.chain().focus().insertContentAt(sel.to, content).run();
  else editor.chain().focus().insertContent(content).run();
}
