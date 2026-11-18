import { z } from "zod";
import { zTipTapDoc } from "../../assistant/protocol";
import { registerCanvasKind } from "../registry";
import { type PdfCanvasData, pdfCanvasKind } from "./pdf";

/*
 * Canvas kinds. Each is registered with its payload schema and a lazy component; S15 adds `pdf` and S15b
 * `generated_media` with one line each here (their own files under kinds/).
 */

const zCell = z.union([z.string(), z.number(), z.null()]);
const zLabelled = z.object({
  key: z.string(),
  label_key: z.string().nullable().optional().default(null),
  label: z.string().nullable().optional().default(null),
});

export const zDocumentData = z.object({
  doc: zTipTapDoc,
  anchor: z.object({ type: z.string(), id: z.string() }).nullable().optional(),
});
export const zDiagramData = z.object({ mermaid: z.string().min(1).max(4000) });
export const zCanvasTableData = z.object({
  columns: z.array(zLabelled).min(1).max(20),
  rows: z.array(z.record(z.string(), zCell)).max(500),
});
export const zCanvasChartData = z.object({
  chart: z.enum(["bar", "line"]),
  x: z.string(),
  series: z.array(zLabelled).min(1).max(8),
  rows: z.array(z.record(z.string(), zCell)).max(500),
  unit: z.enum(["percent", "count", "seconds"]).nullable().optional().default(null),
});
export const zCanvasVideoData = z.object({
  media_id: z.string().min(1),
  provider: z.literal("youtube"),
  video_id: z.string().regex(/^[A-Za-z0-9_-]{6,20}$/),
  start_s: z.number().int().min(0).nullable().optional(),
});

export type DocumentData = z.infer<typeof zDocumentData>;
export type DiagramData = z.infer<typeof zDiagramData>;
export type CanvasTableData = z.infer<typeof zCanvasTableData>;
export type CanvasChartData = z.infer<typeof zCanvasChartData>;
export type CanvasVideoData = z.infer<typeof zCanvasVideoData>;

let registered = false;

/** Register the S14 kinds once (idempotent); returns nothing to undo: they stay for the app's lifetime. */
export function registerBuiltInCanvasKinds(): void {
  if (registered) return;
  registered = true;
  registerCanvasKind<DocumentData>({
    kind: "document",
    schema: zDocumentData,
    load: () => import("./document"),
    label_key: "canvas.kind.document",
    icon: "file-text",
  });
  registerCanvasKind<DiagramData>({
    kind: "diagram",
    schema: zDiagramData,
    load: () => import("./diagram"),
    label_key: "canvas.kind.diagram",
    icon: "workflow",
  });
  registerCanvasKind<CanvasTableData>({
    kind: "table",
    schema: zCanvasTableData,
    load: () => import("./table"),
    label_key: "canvas.kind.table",
    icon: "table-2",
  });
  registerCanvasKind<CanvasChartData>({
    kind: "chart",
    schema: zCanvasChartData,
    load: () => import("./chart"),
    label_key: "canvas.kind.chart",
    icon: "chart-column",
  });
  registerCanvasKind<CanvasVideoData>({
    kind: "video",
    schema: zCanvasVideoData,
    load: () => import("./video"),
    label_key: "canvas.kind.video",
    icon: "film",
  });
  registerCanvasKind<PdfCanvasData>(pdfCanvasKind);
}
