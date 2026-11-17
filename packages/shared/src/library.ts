import { z } from "zod";

/** Payload of the `pdf` canvas kind (segment 15): the student's own PDF upload and the page to open at. Only ids
 * travel; the viewer fetches a fresh signed URL for the file. Also the shape of an assistant page citation. */
export const PdfCanvasDataSchema = z.object({
  upload_id: z.uuid(),
  // Ink is kept for pages 1..10 000 (the ink API's PDF_MAX_PAGE); uploads are capped well below that.
  page: z.number().int().min(1).max(10_000).optional(),
});
export type PdfCanvasData = z.infer<typeof PdfCanvasDataSchema>;
