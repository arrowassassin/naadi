import { z } from "zod";
import { type CanvasRef, zCanvasRef } from "../canvas/registry";

/*
 * The tutor wire protocol (docs/segments/14-protocol.md), validated on arrival. A payload that does not parse is
 * ignored (never rendered half-way, never thrown into the UI): the parse helpers return null / drop the item.
 * The server sends structured fields only; every sentence the student reads is a ui_copy key worded here.
 */

// ── TipTap JSON (rendered by ContentRenderer, which checks every attribute where it is used) ──

export interface TipTapNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: TipTapNode[];
  marks?: { type: string; attrs?: Record<string, unknown> }[];
  text?: string;
}

export const zTipTapNode: z.ZodType<TipTapNode> = z.lazy(() =>
  z.object({
    type: z.string().min(1),
    attrs: z.record(z.string(), z.unknown()).optional(),
    content: z.array(zTipTapNode).optional(),
    marks: z.array(z.object({ type: z.string(), attrs: z.record(z.string(), z.unknown()).optional() })).optional(),
    text: z.string().optional(),
  }),
);

export const zTipTapDoc = z.object({ type: z.literal("doc"), content: z.array(zTipTapNode) });
export type TipTapDoc = z.infer<typeof zTipTapDoc>;

// ── Input ──

export const PAGE_TYPES = [
  "section",
  "question",
  "flashcard",
  "note",
  "search",
  "today",
  "practice",
  "cards",
  "page",
  // the Videos player: id is the media id, the position travels in `video`
  "video",
  // S15: a page of a PDF open in the canvas (id "{upload_id}:{page}"); get_page reads it through S15's resolver.
  "pdf_page",
] as const;
export type PageType = (typeof PAGE_TYPES)[number];

export interface PageContextIn {
  route: string;
  type: PageType;
  id?: string | null;
  title?: string | null;
  chapter_id?: string | null;
  video?: { media_id: string; t: number } | null;
  /** The question id only: the server derives answered/chosen from the student's attempts. */
  question?: { id: string } | null;
}

export type AttachmentIn =
  | { kind: "selection"; text: string; surface: { type: "section" | "note" | "question"; id: string } }
  | { kind: "image"; data_url: string; text?: string | null; surface?: { type: string; id: string } | null };

export type AskKindIn = "hint" | "explain" | "ask" | "summarise" | "flashcards" | "quiz";

export interface TutorInput {
  messages: [{ type: "human"; content: string }];
  page_context: PageContextIn | null;
  attachments?: AttachmentIn[];
  web?: boolean;
  ask?: { kind: AskKindIn };
}

export const MAX_MESSAGE_CHARS = 4000;
export const MAX_SELECTION_CHARS = 4000;
export const MAX_TITLE_CHARS = 200;

// ── Citations, notices ──

export const zCitation = z.object({
  id: z.string().min(1),
  message_id: z.string().nullable(),
  title: z.string(),
  source: z.string(),
  layer: z.enum(["course", "notes", "web"]),
  url: z.string().nullable(),
  target: z.record(z.string(), z.string().nullable()).nullable(),
  license: z.string().nullable(),
  /** The [n] marker the model writes in its text. */
  n: z.number().int().positive().optional(),
});
export type Citation = z.infer<typeof zCitation>;

export const zNotice = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("disclaimer") }),
  z.object({ kind: z.literal("budget_exhausted"), reset_at: z.string() }),
  z.object({ kind: z.literal("rate_limited"), reset_at: z.string() }),
  z.object({ kind: z.literal("unavailable") }),
  z.object({ kind: z.literal("turn_cap") }),
  z.object({ kind: z.literal("exam_mode") }),
]);
export type Notice = z.infer<typeof zNotice>;

// ── Blocks ──

const zCell = z.union([z.string(), z.number(), z.null()]);
const zRows = z.array(z.record(z.string(), zCell)).max(500);
const zLabelled = z.object({ key: z.string(), label_key: z.string().nullable(), label: z.string().nullable() });

export const zFlowchartData = z.object({ mermaid: z.string().min(1).max(4000) });
export const zImageData = z.object({
  url: z.string().startsWith("https://"),
  title: z.string(),
  license: z.string().nullable(),
  credit: z.string().nullable(),
  link: z.string().nullable(),
  width: z.number().positive().nullable().optional(),
  height: z.number().positive().nullable().optional(),
});
const videoBase = {
  media_id: z.string().min(1),
  title: z.string(),
  start_s: z.number().int().min(0).nullable(),
  /** Where the video lives in the Videos catalogue (absent for a chapter video outside it). */
  step: z.enum(["step1", "step2ck"]).nullable().optional(),
  subject: z.string().nullable().optional(),
};
export const zVideoLicence = z.object({
  license_short_name: z.string().nullable().optional(),
  license_url: z.string().nullable().optional(),
  artist: z.string().nullable().optional(),
});
export const zVideoData = z.discriminatedUnion("provider", [
  z.object({ ...videoBase, provider: z.literal("youtube"), video_id: z.string().regex(/^[A-Za-z0-9_-]{6,20}$/) }),
  z.object({
    ...videoBase,
    provider: z.literal("commons"),
    file_url: z.string(),
    page_url: z.string().nullable(),
    attribution: z.string().nullable(),
    duration_s: z.number().nullable(),
    licence: zVideoLicence.nullable(),
  }),
]);
export const zSourcesData = z.object({ items: z.array(zCitation) });
export const zAction = z.object({
  action: z.enum(["open_block", "open_canvas", "open_library", "ask"]),
  label_key: z.string().min(1),
  payload: z.record(z.string(), z.unknown()),
});
export const zActionsData = z.object({ items: z.array(zAction) });
export const zCard = z.object({ front: z.string(), back: z.string() });
export const zCardPreviewData = z.object({ cards: z.array(zCard) });
export const zTableData = z.object({ columns: z.array(zLabelled).min(1).max(20), rows: zRows });
export const zChartData = z.object({
  chart: z.enum(["bar", "line"]),
  x: z.string(),
  series: z.array(zLabelled).min(1).max(8),
  rows: zRows,
  unit: z.enum(["percent", "count", "seconds"]).nullable(),
});

export type FlowchartData = z.infer<typeof zFlowchartData>;
export type ImageData = z.infer<typeof zImageData>;
export type VideoData = z.infer<typeof zVideoData>;
export type ActionItem = z.infer<typeof zAction>;
export type TableData = z.infer<typeof zTableData>;
export type ChartData = z.infer<typeof zChartData>;
export type CardItem = z.infer<typeof zCard>;

const blockBase = { id: z.string().min(1), message_id: z.string().nullable() };

export const zBlock = z.discriminatedUnion("type", [
  z.object({ ...blockBase, type: z.literal("flowchart"), data: zFlowchartData }),
  z.object({ ...blockBase, type: z.literal("image"), data: zImageData }),
  z.object({ ...blockBase, type: z.literal("video"), data: zVideoData }),
  z.object({ ...blockBase, type: z.literal("sources"), data: zSourcesData }),
  z.object({ ...blockBase, type: z.literal("actions"), data: zActionsData }),
  z.object({ ...blockBase, type: z.literal("card_preview"), data: zCardPreviewData }),
  z.object({ ...blockBase, type: z.literal("table"), data: zTableData }),
  z.object({ ...blockBase, type: z.literal("chart"), data: zChartData }),
  z.object({ ...blockBase, type: z.literal("canvas"), data: zCanvasRef }),
  z.object({ ...blockBase, type: z.literal("notice"), data: zNotice }),
]);
export type Block = z.infer<typeof zBlock>;
export type BlockOf<T extends Block["type"]> = Extract<Block, { type: T }>;

// ── Custom events ──

export const TOOL_NAMES = [
  "search_knowledge",
  "get_page",
  "web_search",
  "web_extract",
  "get_analytics",
  "render_diagram",
  "suggest_video",
  "open_in_canvas",
  "save_note",
  "append_to_note",
  "update_note",
  "make_flashcards",
  "build_block",
  "add_to_plan",
  "edit_chapter_section",
  "remember",
  "forget",
] as const;

export const zToolEvent = z.object({
  type: z.literal("tool"),
  tool: z.string().min(1).max(64),
  status: z.enum(["running", "done", "error", "declined"]),
});
export type ToolEvent = z.infer<typeof zToolEvent>;

export type TutorEvent = { type: "block"; block: Block } | { type: "citation"; citation: Citation } | ToolEvent;

/** A custom event as the tutor sends it, or null (ignored) when it does not parse. */
export function parseCustomEvent(data: unknown): TutorEvent | null {
  if (typeof data !== "object" || data === null) return null;
  const type = (data as { type?: unknown }).type;
  if (type === "block") {
    const b = zBlock.safeParse((data as { block?: unknown }).block);
    return b.success ? { type: "block", block: b.data } : null;
  }
  if (type === "citation") {
    const c = zCitation.safeParse((data as { citation?: unknown }).citation);
    return c.success ? { type: "citation", citation: c.data } : null;
  }
  if (type === "tool") {
    const t = zToolEvent.safeParse(data);
    return t.success ? t.data : null;
  }
  return null;
}

// ── Interrupts ──

const zChapterOp = z.object({
  op: z.enum(["replace", "insert_after", "delete"]),
  index: z.number().int().min(0),
  before: zTipTapNode.nullable(),
  after: zTipTapNode.nullable(),
});

export const zPreview = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("note"),
    mode: z.enum(["create", "append", "update"]),
    note_id: z.string().nullable(),
    anchor: z.object({ type: z.string(), id: z.string().nullable(), label: z.string().nullable() }),
    doc: zTipTapDoc,
  }),
  z.object({ kind: z.literal("cards"), cards: z.array(zCard).min(1).max(50) }),
  z.object({
    kind: z.literal("block"),
    count: z.number().int().positive(),
    mode: z.enum(["tutor", "timed"]),
    systems: z.array(z.string()),
    disciplines: z.array(z.string()),
    status: z.enum(["unused", "incorrect", "flagged", "correct", "all"]),
  }),
  z.object({
    kind: z.literal("plan_item"),
    day: z.string(),
    title: z.string(),
    item_kind: z.string(),
    target_minutes: z.number().nullable(),
  }),
  z.object({
    kind: z.literal("chapter_diff"),
    section_id: z.string(),
    heading: z.string(),
    target: z.enum(["revision", "overlay_note"]),
    ops: z.array(zChapterOp).min(1),
  }),
  z.object({ kind: z.literal("memory"), key: z.string(), fact: z.string() }),
  z.object({ kind: z.literal("forget"), key: z.string(), fact: z.string() }),
  z.object({
    kind: z.literal("knowledge"),
    source: z.enum(["link", "file"]),
    label: z.string(),
    host: z.string().nullable(),
  }),
]);
export type Preview = z.infer<typeof zPreview>;
export type PreviewOf<K extends Preview["kind"]> = Extract<Preview, { kind: K }>;

export const zConfirmRequest = z.object({
  type: z.literal("confirm"),
  tool: z.string().min(1),
  preview: zPreview,
  editable: z.boolean(),
});
export type ConfirmRequest = z.infer<typeof zConfirmRequest>;

export function parseInterrupt(value: unknown): ConfirmRequest | null {
  const r = zConfirmRequest.safeParse(value);
  return r.success ? r.data : null;
}

export type EditArgs =
  | { doc: TipTapDoc }
  | { cards: CardItem[] }
  | { day: string; title: string }
  | { fact: string }
  | Record<string, unknown>;

export type ResumeValue = { decision: "accept" } | { decision: "cancel" } | { decision: "edit"; args: EditArgs };

// ── Values (a reopened thread renders everything from the checkpoint) ──

export interface TutorValues {
  blocks: Block[];
  citations: Citation[];
  canvas: CanvasRef[];
  notice: Notice | null;
  refusal: { reason: "exam_mode" } | null;
  disclaimer_shown: boolean;
}

function parseList<T>(raw: unknown, schema: z.ZodType<T>): T[] {
  if (!Array.isArray(raw)) return [];
  const out: T[] = [];
  for (const item of raw) {
    const r = schema.safeParse(item);
    if (r.success) out.push(r.data);
  }
  return out;
}

/** The tutor's state fields the UI reads; anything that does not parse is dropped, item by item. */
export function parseValues(raw: unknown): TutorValues {
  const v = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const notice = zNotice.safeParse(v.notice);
  const refusal = z.object({ reason: z.literal("exam_mode") }).safeParse(v.refusal);
  return {
    blocks: parseList(v.blocks, zBlock),
    citations: parseList(v.citations, zCitation),
    canvas: parseList(v.canvas, zCanvasRef),
    notice: notice.success ? notice.data : null,
    refusal: refusal.success ? refusal.data : null,
    disclaimer_shown: v.disclaimer_shown === true,
  };
}

// ── REST (/api/assistant) ──

export const zThread = z.object({
  id: z.string(),
  aegra_thread_id: z.string().nullable(),
  title: z.string(),
  created_at: z.string(),
  last_message_at: z.string().nullable(),
  page_ref: z.record(z.string(), z.unknown()).nullable(),
});
export type ThreadItem = z.infer<typeof zThread>;
export const zThreadList = z.object({ items: z.array(zThread) });

export const zMemory = z.object({ key: z.string(), fact: z.string(), created_at: z.string() });
export type MemoryItem = z.infer<typeof zMemory>;
export const zMemories = z.object({
  items: z.array(zMemory),
  suggestions: z.array(zMemory),
  max_items: z.number().int().nonnegative(),
});
export type Memories = z.infer<typeof zMemories>;
