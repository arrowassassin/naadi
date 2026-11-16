/**
 * Ink stroke data model, shared by the ink engine (12a), persistence (12b), notebooks and handwriting OCR (13)
 * and offline sync (18). Persisted one item per row in `ink_items.item` (jsonb, migration 0009) and merged by the
 * ink API with mergeInkItems' order; the API's pydantic mirror is checked against fixtures/ink (parity tests).
 *
 * Units: coordinates and sizes are **surface units (su)**. The surface's content-box width is 1.0 on
 * both axes (y is divided by the width too), so the aspect ratio is kept and ink scales uniformly when
 * the surface is resized, zoomed or rotated. `ink.schema.json` is generated from these schemas (see
 * ink.schema.test.ts) so the API side can validate the same shape without hand-written drift.
 */
import "./zod-config";
import * as z from "zod";

export const INK_SCHEMA_VERSION = 1;
/** Decimal places kept for su values: 1e-5 su is about 0.01 px on a 1180 px surface. */
export const INK_PRECISION = 5;
export const INK_MAX_POINTS = 10_000;
export const INK_MAX_ITEMS = 20_000;
export const INK_MAX_TEXT = 2_000;

export const InkSurfaceTypeSchema = z.enum([
  "section",
  "question",
  "flashcard",
  "pdf_page",
  "notebook_page",
  "exam_scratch",
]);
export type InkSurfaceType = z.infer<typeof InkSurfaceTypeSchema>;

/**
 * Colour ROLES, never hex: the renderer resolves them from the design tokens for the surface theme.
 * ink = the student's own ink (ground on paper, paper on dark) · violet = second pen colour
 * (ink-violet on paper) · lime / amber = marker colours with the fixed highlight meanings
 * (key fact / my mistake).
 */
export const InkColorSchema = z.enum(["ink", "violet", "lime", "amber"]);
export type InkColor = z.infer<typeof InkColorSchema>;

export const InkShapeSchema = z.enum(["line", "arrow", "circle", "ellipse", "rect"]);
export type InkShape = z.infer<typeof InkShapeSchema>;

const UUID_V7_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const InkIdSchema = z.string().regex(UUID_V7_RE, "must be a lowercase UUIDv7");

const su = z.number().min(-10_000).max(10_000);
const epochMs = z.number().int().min(0);

/** [x, y, pressure 0..1, t = integer ms since the item's createdAt] */
export const InkPointSchema = z.tuple([su, su, z.number().min(0).max(1), z.number().int().min(0).max(86_400_000)]);
export type InkPoint = z.infer<typeof InkPointSchema>;

/** Text-anchored margin notes: a ProseMirror document position plus an optional su offset. */
export const InkAnchorSchema = z.strictObject({
  pm: z.number().int().min(0),
  dx: su.optional(),
  dy: su.optional(),
});
export type InkAnchor = z.infer<typeof InkAnchorSchema>;

const itemBase = {
  id: InkIdSchema,
  color: InkColorSchema,
  anchor: InkAnchorSchema.optional(),
  createdAt: epochMs,
  /** Bumped on every change (move, recolour, edit); the last-writer-wins key for sync. */
  updatedAt: epochMs,
  /** Tombstone for sync; never rendered. */
  deleted: z.literal(true).optional(),
};

export const InkStrokeSchema = z.strictObject({
  ...itemBase,
  tool: z.enum(["pen", "marker"]),
  size: z.number().gt(0).max(0.1),
  points: z.array(InkPointSchema).min(1).max(INK_MAX_POINTS),
  /** Set when the stroke was auto-straightened; `points` then hold the clean geometry. */
  shape: InkShapeSchema.optional(),
});
export type InkStroke = z.infer<typeof InkStrokeSchema>;

export const InkTextBoxSchema = z.strictObject({
  ...itemBase,
  tool: z.literal("text"),
  /** Font size in su. */
  size: z.number().gt(0).max(0.2),
  x: su,
  y: su,
  w: z.number().gt(0).max(4),
  text: z.string().min(1).max(INK_MAX_TEXT),
});
export type InkTextBox = z.infer<typeof InkTextBoxSchema>;

export const InkItemSchema = z.discriminatedUnion("tool", [InkStrokeSchema, InkTextBoxSchema]);
export type InkItem = z.infer<typeof InkItemSchema>;

/** A whole layer (the items of one surface). Ids are unique. */
export const InkItemsSchema = z
  .array(InkItemSchema)
  .max(INK_MAX_ITEMS)
  .refine((items) => new Set(items.map((i) => i.id)).size === items.length, "item ids must be unique");

/** Payload of `onStrokesChange`: what changed in one user action (or undo/redo). */
export const InkDiffSchema = z.strictObject({
  added: z.array(InkItemSchema),
  updated: z.array(InkItemSchema),
  removed: z.array(InkIdSchema),
});
export type InkDiff = z.infer<typeof InkDiffSchema>;

export function roundSu(v: number): number {
  const f = 10 ** INK_PRECISION;
  const r = Math.round(v * f) / f;
  return Object.is(r, -0) ? 0 : r;
}

// ── UUIDv7 (RFC 9562): 48-bit ms timestamp, then random bits; monotonic within one millisecond. ──

let lastMs = -1;
let lastSeq = 0;

function randomBytes(n: number): Uint8Array {
  const b = new Uint8Array(n);
  globalThis.crypto.getRandomValues(b);
  return b;
}

export function uuidv7(now: number = Date.now()): string {
  const bytes = randomBytes(16);
  let ms = Math.floor(now);
  if (ms <= lastMs) {
    // Same (or a skewed earlier) millisecond: keep ids sortable by bumping the 12-bit sequence.
    ms = lastMs;
    lastSeq += 1;
    if (lastSeq > 0xfff) {
      ms += 1;
      lastSeq = 0;
    }
  } else {
    lastSeq = (((bytes[6] ?? 0) & 0x07) << 8) | (bytes[7] ?? 0); // start low so the sequence has room
  }
  lastMs = ms;
  let t = ms;
  for (let i = 5; i >= 0; i--) {
    bytes[i] = t % 256;
    t = Math.floor(t / 256);
  }
  bytes[6] = 0x70 | ((lastSeq >> 8) & 0x0f);
  bytes[7] = lastSeq & 0xff;
  bytes[8] = 0x80 | ((bytes[8] ?? 0) & 0x3f);
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function isUuidV7(id: string): boolean {
  return UUID_V7_RE.test(id);
}

/** The newer of two versions of the same item: higher updatedAt; on a tie a tombstone wins, then the larger JSON. */
function newer(a: InkItem, b: InkItem): InkItem {
  if (a.updatedAt !== b.updatedAt) return a.updatedAt > b.updatedAt ? a : b;
  if (Boolean(a.deleted) !== Boolean(b.deleted)) return a.deleted ? a : b;
  return JSON.stringify(a) >= JSON.stringify(b) ? a : b;
}

/**
 * Merge two replicas of a layer by item id (last writer wins per item, tombstones included), so the
 * result is the same whichever side merges. Order: by id, which for UUIDv7 is creation order.
 */
export function mergeInkItems(a: readonly InkItem[], b: readonly InkItem[]): InkItem[] {
  const byId = new Map<string, InkItem>();
  for (const item of [...a, ...b]) {
    const prev = byId.get(item.id);
    byId.set(item.id, prev ? newer(prev, item) : item);
  }
  return [...byId.values()].sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
}

/** Apply a diff to a list of items, keeping order (added items go to the end). */
export function applyInkDiff(items: readonly InkItem[], diff: InkDiff): InkItem[] {
  const removed = new Set(diff.removed);
  const updated = new Map(diff.updated.map((i) => [i.id, i]));
  const out: InkItem[] = [];
  for (const item of items) {
    if (removed.has(item.id)) continue;
    out.push(updated.get(item.id) ?? item);
  }
  const present = new Set(out.map((i) => i.id));
  for (const item of diff.added) if (!present.has(item.id)) out.push(item);
  return out;
}

/**
 * Record a diff into a sync replica (12b/18): like applyInkDiff, but removed items become tombstones
 * stamped `now`, so the deletion itself wins last-writer-wins merges against older copies.
 */
export function recordInkDiff(items: readonly InkItem[], diff: InkDiff, now: number): InkItem[] {
  const removed = new Set(diff.removed);
  const updated = new Map([...diff.updated, ...diff.added].map((i) => [i.id, i]));
  const out: InkItem[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    seen.add(item.id);
    if (removed.has(item.id)) {
      out.push({ ...item, deleted: true, updatedAt: Math.max(Math.floor(now), item.updatedAt + 1) });
      continue;
    }
    out.push(updated.get(item.id) ?? item);
  }
  for (const item of diff.added) if (!seen.has(item.id)) out.push(item);
  return out;
}

/** JSON Schema of a layer's items, for the API side (its pydantic mirror is checked against it). */
export function inkItemsJsonSchema(): Record<string, unknown> {
  return z.toJSONSchema(z.array(InkItemSchema).max(INK_MAX_ITEMS), { target: "draft-2020-12" }) as Record<
    string,
    unknown
  >;
}
