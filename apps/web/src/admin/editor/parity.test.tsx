import {
  CONTENT_MARK_TYPES,
  CONTENT_NODE_TYPES,
  ContentDocSchema,
  collectTypes,
  contentExtensions,
} from "@naadi/shared/content";
import { render } from "@testing-library/react";
import { Editor, getSchema } from "@tiptap/react";
import { afterEach, describe, expect, it } from "vitest";
import { ContentRenderer } from "../../content/ContentRenderer";
import { RENDERED_NODE_TYPES } from "../../content/types";
import { ATOM_NODE_TYPES, editorExtensions } from "./extensions";
import { validateAtom } from "./NodeDialog";
import { invalidPath } from "./useAutosave";

const editors: Editor[] = [];
function editor(content: unknown = { type: "doc", content: [{ type: "paragraph" }] }) {
  const e = new Editor({
    extensions: editorExtensions({ nodeViews: false }),
    content: content as object,
    injectCSS: false,
  });
  editors.push(e);
  return e;
}
afterEach(() => {
  for (const e of editors.splice(0)) e.destroy();
});

const ATOM_ATTRS: Record<string, Record<string, unknown>> = {
  figure: { mediaId: "fig-1" },
  video: { mediaId: "vid-1", startS: 30, endS: 90 },
  pdf: { mediaId: "pdf-1", page: 3 },
  questionEmbed: { questionId: "q-1" },
  mermaid: { code: "graph TD; A-->B" },
  math: { latex: "\\frac{a}{b}" },
};

/** A fresh paragraph at the end of the document with the cursor in it (each block starts outside the last). */
function newBlock(e: Editor, text: string) {
  const end = e.state.doc.content.size;
  e.chain()
    .insertContentAt(end, { type: "paragraph", content: [{ type: "text", text }] })
    .run();
  const size = e.state.doc.content.size;
  e.chain()
    .focus()
    .setTextSelection(size - 1)
    .run();
}

/** Builds a document with every node and mark through the editor's own commands, as an admin would. */
function authorEverything(e: Editor) {
  e.chain().focus().insertContent("Intro ").setHardBreak().insertContent("line").run();
  for (const m of ["bold", "italic", "subscript", "superscript"]) {
    e.chain().focus("end").insertContent(" xy").run();
    const end = e.state.selection.from;
    e.chain()
      .setTextSelection({ from: end - 2, to: end })
      .toggleMark(m)
      .run();
  }
  e.chain().focus("end").insertContent(" hl").run();
  const at = e.state.selection.from;
  e.chain()
    .setTextSelection({ from: at - 2, to: at })
    .setMark("highlight", { color: "violet", meaning: "formula" })
    .run();
  newBlock(e, "Head");
  e.chain().setHeading({ level: 3 }).run();
  newBlock(e, "bullets");
  e.chain().toggleBulletList().run();
  newBlock(e, "numbers");
  e.chain().toggleOrderedList().run();
  newBlock(e, "trap");
  e.chain().wrapIn("callout", { variant: "trap" }).run();
  newBlock(e, "table");
  e.chain().insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run();
  for (const type of ATOM_NODE_TYPES) {
    e.chain().insertContentAt(e.state.doc.content.size, { type, attrs: ATOM_ATTRS[type] }).run();
  }
}

describe("editor ↔ ContentRenderer node parity", () => {
  it("the editor's schema has exactly the shared content schema's nodes and marks", () => {
    const shared = getSchema(contentExtensions);
    for (const views of [true, false]) {
      const ed = getSchema(editorExtensions({ nodeViews: views }));
      expect(Object.keys(ed.nodes).sort()).toEqual(Object.keys(shared.nodes).sort());
      expect(Object.keys(ed.marks).sort()).toEqual(Object.keys(shared.marks).sort());
      for (const [name, type] of Object.entries(shared.nodes)) {
        expect(Object.keys(ed.nodes[name]?.spec.attrs ?? {}).sort(), name).toEqual(
          Object.keys(type.spec.attrs ?? {}).sort(),
        );
      }
    }
    expect([...Object.keys(shared.nodes)].sort()).toEqual([...CONTENT_NODE_TYPES].sort());
    expect([...Object.keys(shared.marks)].sort()).toEqual([...CONTENT_MARK_TYPES].sort());
    expect([...RENDERED_NODE_TYPES].sort()).toEqual([...CONTENT_NODE_TYPES].sort());
  });

  it("every content node type is insertable with the toolbar's commands and the result is a valid document", () => {
    const e = editor();
    authorEverything(e);
    const json = e.getJSON();
    const { nodes, marks } = collectTypes(json as never);
    expect([...nodes.keys()].sort()).toEqual([...CONTENT_NODE_TYPES].sort());
    expect([...marks.keys()].sort()).toEqual([...CONTENT_MARK_TYPES].sort());
    expect(invalidPath(json)).toBeNull();
  });

  it("round-trips: the stored JSON loads back into the editor unchanged", () => {
    const first = editor();
    authorEverything(first);
    const json = first.getJSON();
    const again = editor(json).getJSON();
    expect(again).toEqual(json);
    // and the shared (seed / server) schema reads it identically
    const viaShared = new Editor({ extensions: contentExtensions, content: json });
    editors.push(viaShared);
    expect(viaShared.getJSON()).toEqual(json);
  });

  it("ContentRenderer renders every node the editor produced (no unknown-node card)", () => {
    const e = editor();
    authorEverything(e);
    const { container } = render(<ContentRenderer doc={e.getJSON()} sectionId="s-1" mode="dev" />);
    expect(container.querySelector("[data-unknown-node],[data-unknown-mark]")).toBeNull();
    expect(container.querySelector("[data-node='callout'][data-variant='trap']")).not.toBeNull();
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("puts no inline style attribute in the editor DOM (the CSP refuses them), tables and aligned cells included", () => {
    const e = editor();
    e.chain().focus().insertTable({ rows: 2, cols: 3, withHeaderRow: true }).run();
    e.chain()
      .focus()
      .command(({ tr, state }) => {
        state.doc.descendants((n, pos) => {
          if (n.type.name === "tableCell") tr.setNodeMarkup(pos, undefined, { ...n.attrs, align: "center" });
        });
        return true;
      })
      .run();
    expect(e.view.dom.querySelector("[style]")).toBeNull();
    expect(e.view.dom.querySelector("td[data-align='center']")).not.toBeNull();
    expect(ContentDocSchema.safeParse(e.getJSON()).success).toBe(true);
  });

  it("the attribute dialog accepts exactly what the schema accepts", () => {
    for (const type of ATOM_NODE_TYPES) expect(validateAtom(type, ATOM_ATTRS[type] ?? {}), type).toBeNull();
    expect(validateAtom("figure", { mediaId: null })).toBe("admin.editor.err_media");
    expect(validateAtom("figure", { mediaId: "Bad Id" })).toBe("admin.editor.err_media");
    expect(validateAtom("video", { mediaId: "v", startS: 50, endS: 10 })).toBe("admin.editor.err_range");
    expect(validateAtom("pdf", { mediaId: "p", page: 0 })).toBe("admin.editor.err_page");
    expect(validateAtom("mermaid", { code: "  " })).toBe("admin.editor.err_code");
    expect(validateAtom("math", { latex: "x".repeat(5001) })).toBe("admin.editor.err_latex");
    expect(validateAtom("questionEmbed", { questionId: "" })).toBe("admin.editor.err_question");
  });

  it("reports the failing node path of an invalid document", () => {
    expect(invalidPath({ type: "doc", content: [{ type: "figure", attrs: { mediaId: "Bad Id" } }] })).toBe(
      "body.content.0.attrs.mediaId",
    );
    expect(invalidPath({ type: "doc", content: [{ type: "blink" }] })).toMatch(/^body\.content\.0/);
  });
});
