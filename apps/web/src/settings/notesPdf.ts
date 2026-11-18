import {
  getNotebook,
  listNotebooks,
  listNotes,
  type NotebookDetail,
  type NotebookList,
  type NotebookOut,
  type NoteList,
  type NoteOut,
} from "@naadi/shared";
import { authed } from "../auth/api";
import {
  exportHighlightsToPdf,
  exportNotebookToPdf,
  exportNoteToPdf,
  mergePdfs,
  type PdfResult,
  type PdfText,
} from "../pdf";

/**
 * The device half of Export everything: S13's exporter makes one "Notes" PDF (typed notes, then highlights) and one
 * "Notebooks" PDF here in the browser. Nothing is uploaded and no note text is logged.
 */
export type PdfKind = "notes" | "notebooks";

export interface PdfFile {
  kind: PdfKind;
  result: PdfResult;
  /** Notes (or notebooks) that went into it; highlights count as one entry when there are any. */
  items: number;
}

export interface NotesPdfOutcome {
  files: PdfFile[];
  /** What had nothing to export, said to the student in words. */
  empty: ("notes" | "highlights" | "notebooks")[];
}

export interface Progress {
  done: number;
  total: number;
}

export interface NotesPdfDeps {
  fetchNotes(kind: "note" | "highlight"): Promise<NoteOut[]>;
  fetchNotebooks(): Promise<NotebookOut[]>;
  fetchNotebook(id: string): Promise<NotebookDetail>;
  noteToPdf: typeof exportNoteToPdf;
  highlightsToPdf: typeof exportHighlightsToPdf;
  notebookToPdf: typeof exportNotebookToPdf;
  merge: typeof mergePdfs;
  t: PdfText;
}

export const NOTES_FILE = "naadi-notes.pdf";
export const NOTEBOOKS_FILE = "naadi-notebooks.pdf";

function abortError(): DOMException {
  return new DOMException("cancelled", "AbortError");
}

async function combine(parts: PdfResult[], filename: string, deps: NotesPdfDeps): Promise<PdfResult> {
  const [only] = parts;
  if (parts.length === 1 && only) return { ...only, filename };
  return deps.merge(parts, filename, deps.t);
}

/** Make the PDFs. Rejects with an AbortError when `signal` aborts (the student pressed Cancel). */
export async function buildNotesPdfs(
  deps: NotesPdfDeps,
  opts: { signal?: AbortSignal; onProgress?: (p: Progress) => void } = {},
): Promise<NotesPdfOutcome> {
  const { signal, onProgress } = opts;
  const step = <T>(fn: () => Promise<T>): Promise<T> => {
    if (signal?.aborted) throw abortError();
    return fn();
  };
  const [notes, highlights, notebooks] = await Promise.all([
    step(() => deps.fetchNotes("note")),
    step(() => deps.fetchNotes("highlight")),
    step(() => deps.fetchNotebooks()),
  ]);
  const books = notebooks.filter((n) => n.page_count > 0);
  const total = notes.length + (highlights.length ? 1 : 0) + books.length;
  let done = 0;
  const tick = () => onProgress?.({ done: ++done, total });
  onProgress?.({ done: 0, total });

  const files: PdfFile[] = [];
  const empty: NotesPdfOutcome["empty"] = [];
  const noteParts: PdfResult[] = [];
  for (const n of notes) {
    noteParts.push(await step(() => deps.noteToPdf(n, deps.t)));
    tick();
  }
  if (highlights.length) {
    noteParts.push(await step(() => deps.highlightsToPdf(highlights, deps.t)));
    tick();
  } else empty.push("highlights");
  if (notes.length === 0) empty.push("notes");
  if (noteParts.length) {
    files.push({
      kind: "notes",
      result: await step(() => combine(noteParts, NOTES_FILE, deps)),
      items: noteParts.length,
    });
  }

  const bookParts: PdfResult[] = [];
  for (const b of books) {
    const detail = await step(() => deps.fetchNotebook(b.id));
    bookParts.push(await step(() => deps.notebookToPdf(detail, deps.t)));
    tick();
  }
  if (bookParts.length) {
    files.push({
      kind: "notebooks",
      result: await step(() => combine(bookParts, NOTEBOOKS_FILE, deps)),
      items: bookParts.length,
    });
  } else empty.push("notebooks");
  return { files, empty };
}

const PAGE = 200;
const MAX_PAGES = 50;

/** The real sources and exporter. */
export function liveDeps(t: PdfText): NotesPdfDeps {
  return {
    async fetchNotes(kind) {
      const out: NoteOut[] = [];
      let offset: number | null = 0;
      for (let i = 0; offset !== null && i < MAX_PAGES; i++) {
        const o: number = offset;
        const page: NoteList = await authed<NoteList>((headers) =>
          listNotes({ headers, query: { kind, limit: PAGE, offset: o } }),
        );
        out.push(...page.items);
        offset = page.next_offset;
      }
      return out;
    },
    async fetchNotebooks() {
      return (await authed<NotebookList>((headers) => listNotebooks({ headers }))).items;
    },
    fetchNotebook: (id) => authed<NotebookDetail>((headers) => getNotebook({ headers, path: { notebook_id: id } })),
    noteToPdf: exportNoteToPdf,
    highlightsToPdf: exportHighlightsToPdf,
    notebookToPdf: exportNotebookToPdf,
    merge: mergePdfs,
    t,
  };
}
