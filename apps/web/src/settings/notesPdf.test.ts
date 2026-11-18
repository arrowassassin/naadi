import type { NotebookDetail, NotebookOut, NoteOut } from "@naadi/shared";
import { describe, expect, it, vi } from "vitest";
import { buildNotesPdfs, NOTEBOOKS_FILE, NOTES_FILE, type NotesPdfDeps } from "./notesPdf";

const result = (filename: string, pages = 1) => ({ blob: new Blob(["%PDF"]), filename, pages });
const note = (id: string) => ({ id, title: id, body: null, updated_at: "2026-01-01T00:00:00Z" }) as unknown as NoteOut;
const book = (id: string, page_count = 2) => ({ id, title: id, page_count }) as unknown as NotebookOut;

function deps(over: { notes?: string[]; highlights?: string[]; books?: NotebookOut[] } = {}): NotesPdfDeps {
  const { notes = ["n1", "n2"], highlights = ["h1"], books = [book("b1"), book("b2")] } = over;
  return {
    fetchNotes: vi.fn(async (kind) => (kind === "note" ? notes : highlights).map(note)),
    fetchNotebooks: vi.fn(async () => books),
    fetchNotebook: vi.fn(async (id) => ({ id, title: id, paper: "ruled", pages: [] }) as unknown as NotebookDetail),
    noteToPdf: vi.fn(async (n) => result(`${n.title}.pdf`)),
    highlightsToPdf: vi.fn(async () => result("highlights.pdf")),
    notebookToPdf: vi.fn(async (nb) => result(`${nb.title}.pdf`, 3)),
    merge: vi.fn(async (parts, filename) =>
      result(
        filename,
        parts.reduce((n: number, p: { pages: number }) => n + p.pages, 0),
      ),
    ),
    t: (k) => k,
  };
}

describe("notes PDFs on the device", () => {
  it("makes one Notes PDF (notes then highlights) and one Notebooks PDF, counting every step", async () => {
    const d = deps();
    const seen: string[] = [];
    const out = await buildNotesPdfs(d, { onProgress: (p) => seen.push(`${p.done}/${p.total}`) });
    expect(seen).toEqual(["0/5", "1/5", "2/5", "3/5", "4/5", "5/5"]);
    expect(out.files.map((f) => [f.kind, f.result.filename, f.items])).toEqual([
      ["notes", NOTES_FILE, 3],
      ["notebooks", NOTEBOOKS_FILE, 2],
    ]);
    expect(out.empty).toEqual([]);
    expect(d.merge).toHaveBeenCalledTimes(2);
    const mergedNames = vi.mocked(d.merge).mock.calls[0]?.[0].map((p) => p.filename);
    expect(mergedNames).toEqual(["n1.pdf", "n2.pdf", "highlights.pdf"]);
  });

  it("skips what is empty and says so", async () => {
    const out = await buildNotesPdfs(deps({ notes: [], highlights: [], books: [book("empty", 0)] }));
    expect(out.files).toEqual([]);
    expect(out.empty.sort()).toEqual(["highlights", "notebooks", "notes"]);
  });

  it("keeps the notes PDF when only highlights exist, without merging a single part", async () => {
    const d = deps({ notes: [], books: [] });
    const out = await buildNotesPdfs(d);
    expect(out.files.map((f) => f.kind)).toEqual(["notes"]);
    expect(out.files[0]?.result.filename).toBe(NOTES_FILE);
    expect(d.merge).not.toHaveBeenCalled();
    expect(out.empty).toEqual(expect.arrayContaining(["notes", "notebooks"]));
  });

  it("stops when cancelled and makes nothing more", async () => {
    const c = new AbortController();
    const d = deps();
    vi.mocked(d.noteToPdf).mockImplementationOnce(async (n) => {
      c.abort();
      return result(`${n.title}.pdf`);
    });
    await expect(buildNotesPdfs(d, { signal: c.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(d.noteToPdf).toHaveBeenCalledTimes(1);
    expect(d.notebookToPdf).not.toHaveBeenCalled();
    expect(d.merge).not.toHaveBeenCalled();
  });

  it("surfaces a failing exporter instead of a partial file", async () => {
    const d = deps();
    vi.mocked(d.notebookToPdf).mockRejectedValueOnce(new Error("boom"));
    await expect(buildNotesPdfs(d)).rejects.toThrow("boom");
  });
});
