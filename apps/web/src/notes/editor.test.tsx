import { act, fireEvent, render, screen } from "@testing-library/react";
import type { Editor } from "@tiptap/react";
import { describe, expect, it, vi } from "vitest";
import { isEmptyDoc, quoteDoc } from "./extensions";
import { NoteEditor } from "./NoteEditor";
import { quickNoteDoc } from "./QuickNoteDialog";

const doc = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "Anion gap" }] }] };

describe("NoteEditor", () => {
  it("opening a note is not an edit (nothing to save)", async () => {
    const onChange = vi.fn();
    render(<NoteEditor noteId="n" initial={doc} onChange={onChange} label="Note text" placeholder="Write" />);
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Note text" })).toHaveTextContent("Anion gap");
  });

  it("highlights carry their colour as data and token classes, never an inline style (CSP)", async () => {
    let ed: Editor | null = null;
    const onChange = vi.fn();
    render(
      <NoteEditor
        noteId="n"
        initial={doc}
        onChange={onChange}
        label="Note text"
        placeholder="Write"
        onEditor={(e) => {
          ed = e;
        }}
      />,
    );
    await act(() => new Promise((r) => setTimeout(r, 0)));
    act(() => {
      (ed as Editor | null)?.commands.selectAll();
    });
    fireEvent.click(screen.getByRole("button", { name: "Formula" }));
    const mark = document.querySelector("mark[data-color=violet]");
    expect(mark).not.toBeNull();
    expect(mark?.getAttribute("style")).toBeNull();
    expect(onChange).toHaveBeenCalled();
    const last = onChange.mock.calls.at(-1)?.[0];
    expect(JSON.stringify(last)).toContain('"color":"violet"');
    // the three colours each have their word on the toolbar
    for (const w of ["Key fact", "Formula", "My mistake"])
      expect(screen.getByRole("button", { name: w })).toBeVisible();
  });

  it("shows no image button without a storage bucket in the public config", () => {
    render(<NoteEditor noteId="n" initial={doc} onChange={vi.fn()} label="Note text" placeholder="Write" />);
    expect(screen.queryByRole("button", { name: "Add an image" })).toBeNull();
  });
});

describe("note documents", () => {
  it("empty and quote docs", () => {
    expect(isEmptyDoc({ type: "doc", content: [{ type: "paragraph" }] })).toBe(true);
    expect(isEmptyDoc(doc)).toBe(false);
    expect(isEmptyDoc(quoteDoc("q"))).toBe(false);
    expect(isEmptyDoc({ type: "doc", content: [{ type: "image", attrs: { path: "u/x.png" } }] })).toBe(false);
    const q = quickNoteDoc("RTA", "first\n\nsecond");
    expect(q.content).toHaveLength(3);
    expect(JSON.stringify(q)).toContain("blockquote");
  });
});
