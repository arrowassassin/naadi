import { type PdfCanvasData, PdfCanvasDataSchema } from "@naadi/shared";

/**
 * Canvas kind `pdf` (segment 15), shaped like S14's `CanvasKind` contract (apps/web/src/canvas/registry.ts):
 * register it with `registerCanvasKind(pdfCanvasKind)` from `canvas/kinds/index.ts`. The payload is only the
 * student's upload id and the page to open at; the viewer fetches a fresh signed URL, renders with pdf.js, keeps
 * ink per page (`pdf_page` surfaces) and offers lasso → Ask while the assistant is available. Assistant page
 * citations `{upload_id, page}` open the same canvas at that page.
 */
export const pdfCanvasSchema = PdfCanvasDataSchema;
export type { PdfCanvasData };

export const pdfCanvasKind = {
  kind: "pdf",
  schema: pdfCanvasSchema,
  load: () => import("../../library/pdf/PdfCanvas"),
  label_key: "canvas.kind.pdf",
  icon: "file-text",
  requires_flag: "library",
} as const;
