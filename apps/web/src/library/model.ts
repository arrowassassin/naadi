import type { IngestItemOut, IngestOptionOut } from "@naadi/shared";
import type { TextLookup } from "../content/types";
import { count } from "../learn/format";

/**
 * Library logic without React (segment 15): what a card's status word is, which items share a confirm card, how a
 * placement option is worded from its structured preview, and whether the page still needs to poll. Every word
 * comes from ui_copy (`library.*`, `ingest.*`); the server sends codes, counts and the student's own titles only.
 */

export type Item = IngestItemOut;

export type StatusCode =
  | "uploading"
  | "upload_failed"
  | "upload_incomplete"
  | "received"
  | "reading"
  | "needs_you"
  | "personal"
  | "placing"
  | "ready"
  | "failed"
  | "vaulted";

/** What this tab knows that the server does not say yet. */
export interface UploadState {
  /** The file is being uploaded from this tab. */
  isUploading: (id: string) => boolean;
  /** Its upload from this tab failed. */
  hasFailed: (id: string) => boolean;
  /** The student answered its confirm card or Vault prompt and the item has not changed on the server since. */
  isAwaiting: (item: Item) => boolean;
}

/** The card's status (worded as `library.status.<code>`, always with its icon). */
export function statusOf(item: Item, up: UploadState): StatusCode {
  switch (item.status) {
    case "received":
      if (item.stage !== "upload") return "received";
      if (up.isUploading(item.id)) return "uploading";
      return up.hasFailed(item.id) ? "upload_failed" : "upload_incomplete";
    case "extracting":
      return "reading";
    case "needs_confirmation":
      if (up.isAwaiting(item)) return "placing";
      return item.stage === "vault" ? "personal" : "needs_you";
    case "placing":
      return "placing";
    case "ready":
      return "ready";
    case "vaulted":
      return "vaulted";
    default:
      return "failed";
  }
}

/** Still moving on the server: the run is receiving, reading or placing it. */
export function isMoving(code: StatusCode): boolean {
  return code === "received" || code === "reading" || code === "placing";
}

/** A run that has not reported for this long is not waited on any more (the item stays as it is). */
export const STALE_MS = 15 * 60_000;

/**
 * Poll only while some item is moving and has reported recently (or was just answered and its run has not picked
 * the answer up yet); a confirm card that waits for the student needs no polling.
 */
export function needsPolling(items: readonly Item[], up: UploadState, now = Date.now()): boolean {
  return items.some(
    (i) => isMoving(statusOf(i, up)) && (up.isAwaiting(i) || now - Date.parse(i.updated_at) < STALE_MS),
  );
}

// ── filters ──

export const FILTERS = ["all", "files", "links", "pastes", "needs_you", "failed"] as const;
export type Filter = (typeof FILTERS)[number] | "generated";

export function matchesFilter(item: Item, filter: Filter, up: UploadState): boolean {
  const code = statusOf(item, up);
  switch (filter) {
    case "files":
      return item.input_kind !== "link" && item.input_kind !== "paste";
    case "links":
      return item.input_kind === "link";
    case "pastes":
      return item.input_kind === "paste";
    case "needs_you":
      return code === "needs_you" || code === "personal";
    case "failed":
      return code === "failed" || code === "upload_failed" || code === "upload_incomplete";
    case "generated":
      return false;
    default:
      return true;
  }
}

// ── confirm cards ──

export interface ConfirmGroup {
  /** The batch id when the group is a batch of several items (the grouped confirm), else null. */
  batchId: string | null;
  key: string;
  items: Item[];
}

/**
 * Items waiting for a placement, one group per batch (files dropped together share one confirm card); an item
 * without a batch, or alone in its batch, is its own group. Newest group first; items in the order they came.
 */
export function confirmGroups(items: readonly Item[], up: UploadState): ConfirmGroup[] {
  const waiting = items.filter(
    (i) => i.status === "needs_confirmation" && i.stage === "confirm" && i.options.length && !up.isAwaiting(i),
  );
  const byKey = new Map<string, Item[]>();
  for (const i of waiting) {
    const key = i.batch_id ? `batch:${i.batch_id}` : `item:${i.id}`;
    const list = byKey.get(key);
    if (list) list.push(i);
    else byKey.set(key, [i]);
  }
  const groups = [...byKey.entries()].map(([key, list]) => {
    const sorted = [...list].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    const first = sorted[0] as Item;
    return { key, batchId: sorted.length > 1 ? first.batch_id : null, items: sorted };
  });
  // Items are sorted oldest first, so a group's newest item is its last.
  const newest = (g: ConfirmGroup) => g.items.at(-1)?.created_at ?? "";
  return groups.sort((a, b) => newest(b).localeCompare(newest(a)));
}

/** Items waiting at the Vault prompt (one card each: a personal document is never decided in bulk). */
export function vaultPrompts(items: readonly Item[], up: UploadState): Item[] {
  return items.filter((i) => i.status === "needs_confirmation" && i.stage === "vault" && !up.isAwaiting(i));
}

/** The option preselected on a confirm card: the recommended one, else the first. */
export function defaultOption(item: Item): IngestOptionOut | undefined {
  return item.options.find((o) => o.recommended) ?? item.options[0];
}

// ── wording ──

/** Placement kinds with their own wording; kinds registered later (S16/S17/S20) use the generic key. */
export const PLACEMENT_KINDS = [
  "private_chapter",
  "kb_entry",
  "figure_overlay",
  "notebook_page",
  "flashcards",
  "private_questions",
] as const;
const KNOWN_KINDS = new Set<string>(PLACEMENT_KINDS);

export function placementKindKey(kind: string): string {
  return KNOWN_KINDS.has(kind) ? kind : "other";
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.round(v) : null;
}

export interface OptionWording {
  /** The headline, e.g. "New chapter: Renal physiology". */
  title: string;
  /** What it contains, e.g. "4 sections · 12 pages", or "" when the preview has no counts. */
  detail: string;
}

/** Word one placement option from its structured preview (`ingest.placement.<kind>` and `ingest.preview.*`). */
export function optionWording(t: TextLookup, option: IngestOptionOut, itemName: string | null): OptionWording {
  const p = option.preview ?? {};
  const kind = placementKindKey(option.kind);
  const title = str(p.title) || str(p.deck) || itemName || t("library.untitled");
  const vars: Record<string, string> = {
    title,
    deck: str(p.deck) || title,
    chapter: str(p.chapter_title) || t("library.untitled"),
    heading: str(p.heading) || t("library.untitled"),
  };
  const parts: string[] = [];
  const add = (base: string, v: unknown) => {
    const n = num(v);
    if (n) parts.push(count(t, base, n));
  };
  switch (kind) {
    case "private_chapter":
      add("ingest.preview.sections", p.sections);
      add("ingest.preview.pages", p.pages);
      add("ingest.preview.figures", p.figures);
      break;
    case "kb_entry":
      add("ingest.preview.words", p.words);
      if (str(p.url)) parts.push(t("ingest.preview.from_link"));
      break;
    case "figure_overlay":
      if (str(p.caption)) parts.push(t("ingest.preview.caption", { caption: str(p.caption) }));
      break;
    case "notebook_page":
      add("ingest.preview.words", p.words);
      if (str(p.heading)) parts.push(t("ingest.preview.linked_to", { heading: str(p.heading) }));
      break;
    case "flashcards":
      add("ingest.preview.cards", p.count);
      break;
    case "private_questions":
      add("ingest.preview.questions", p.count);
      break;
    default:
      add("ingest.preview.items", p.count);
  }
  return { title: t(`ingest.placement.${kind}`, vars), detail: parts.join(t("library.separator")) };
}

/**
 * The line at the top of a batch's confirm card: per chosen kind, how many files go where, e.g.
 * "2 files → new chapters · 1 file → figures".
 */
export function batchSummary(t: TextLookup, items: readonly Item[], chosen: ReadonlyMap<string, string>): string {
  const perKind = new Map<string, number>();
  for (const i of items) {
    const id = chosen.get(i.id);
    const option = i.options.find((o) => o.option_id === id) ?? defaultOption(i);
    if (!option) continue;
    const k = placementKindKey(option.kind);
    perKind.set(k, (perKind.get(k) ?? 0) + 1);
  }
  return [...perKind.entries()]
    .map(([k, n]) =>
      t("ingest.batch_line", { files: count(t, "library.count_file", n), place: t(`ingest.place.${k}`) }),
    )
    .join(t("library.separator"));
}

/** Bytes as "820 KB", "12.4 MB", "1.2 GB" (units from ui_copy). */
export function sizeLabel(t: TextLookup, bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "";
  const kb = bytes / 1024;
  const mb = kb / 1024;
  const gb = mb / 1024;
  // One decimal below 1000 ("599.6 MB left", never a rounded-up "600 MB left"); whole numbers above.
  const fmt = (v: number) => (v >= 1000 ? String(Math.floor(v)) : String(Math.floor(v * 10) / 10));
  if (gb >= 1) return t("library.size_gb", { n: fmt(gb) });
  if (mb >= 1) return t("library.size_mb", { n: fmt(mb) });
  return t("library.size_kb", { n: fmt(Math.max(kb, 0.1)) });
}

/** The card's second line: kind, size and what was found ("PDF · 2.1 MB · 12 pages"). */
export function metaLine(t: TextLookup, item: Item): string {
  const parts = [t(`library.kind.${item.input_kind ?? "file"}`)];
  const size = sizeLabel(t, item.bytes);
  if (size) parts.push(size);
  const pages = item.pages ?? item.counts?.pages ?? null;
  if (pages) parts.push(count(t, "ingest.preview.pages", pages));
  else if (item.counts?.words) parts.push(count(t, "ingest.preview.words", item.counts.words));
  if (item.bundle_files) parts.push(count(t, "library.count_file", item.bundle_files));
  return parts.join(t("library.separator"));
}

/** Sensitivity signal codes the local check reports (naadi_api/ingest/sensitivity.py). */
export const SIGNALS = [
  "passport_mrz",
  "aadhaar",
  "pan",
  "ssn",
  "sevis",
  "visa_number",
  "doc_passport",
  "doc_id_card",
  "doc_diploma",
  "doc_visa",
  "doc_i20",
  "doc_ds2019",
  "doc_receipt",
] as const;
const KNOWN_SIGNALS = new Set<string>(SIGNALS);

export function signalKey(code: string): string {
  return `ingest.signal.${KNOWN_SIGNALS.has(code) ? code : "other"}`;
}

export const INPUT_KINDS = [
  "pdf",
  "docx",
  "pptx",
  "xlsx",
  "csv",
  "txt",
  "md",
  "html",
  "epub",
  "rtf",
  "image",
  "audio",
  "video",
  "link",
  "anki",
  "zip",
  "paste",
  "file",
] as const;
