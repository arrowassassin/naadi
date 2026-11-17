/**
 * PDF export (segment 13), the public entry: a light façade whose functions load the export chunk (pdf-lib,
 * fontkit, layout, fonts; see export.ts) on first use, so the app's main bundle carries none of it.
 *
 *   exportNoteToPdf(note, t)            a typed note (TipTap) with highlight tints and a word legend
 *   exportHighlightsToPdf(list, t)      a filtered list of highlights: quote, colour word, source, note
 *   exportNotebookToPdf(notebook, t)    one US Letter page per notebook page: paper + vector ink, or whiteboard
 *   deliverPdf(result)                  download (Blob + anchor), or the share sheet where there is no download
 *
 * Nothing is uploaded. `t` is the app's ui_copy function: every word in the PDF comes from it.
 */
import type { NotebookDetail, NoteOut } from "@naadi/shared";
import type { NotebookExportSources, PdfResult, PdfText } from "./export";

export type { NotebookExportSources, PdfResult, PdfText } from "./export";

const load = () => import("./export");

export async function exportNoteToPdf(
  note: Pick<NoteOut, "title" | "body" | "updated_at">,
  t: PdfText,
): Promise<PdfResult> {
  return (await load()).exportNoteToPdf(note, t);
}

export async function exportHighlightsToPdf(
  highlights: readonly NoteOut[],
  t: PdfText,
  opts: { title?: string } = {},
): Promise<PdfResult> {
  return (await load()).exportHighlightsToPdf(highlights, t, opts);
}

export async function exportNotebookToPdf(
  notebook: Pick<NotebookDetail, "title" | "paper" | "pages">,
  t: PdfText,
  sources: NotebookExportSources = {},
): Promise<PdfResult> {
  return (await load()).exportNotebookToPdf(notebook, t, sources);
}

export async function deliverPdf(result: PdfResult): Promise<"download" | "share"> {
  return (await load()).deliverPdf(result);
}

/** Start loading the export chunk early (e.g. when an Export button is shown), so the click feels instant. */
export function preloadPdfExport(): void {
  void load().catch(() => undefined);
}
