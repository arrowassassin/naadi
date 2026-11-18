import type { ComponentType } from "react";
import { useSyncExternalStore } from "react";
import { type ZodType, z } from "zod";

/*
 * Canvas registry (S14 contract, ledger 20261117T033403). The canvas is the panel where the assistant puts things
 * the student keeps working with: a drafted document, a diagram, a table, a chart, a video; S15 adds `pdf` and
 * S15b `generated_media` through `registerCanvasKind`. Layout (side panel ≥ 1100 px and iPad landscape,
 * full-screen sheet otherwise), close/tab chrome, violet/paper framing and the error/empty states belong to the
 * host (CanvasHost.tsx), never to a kind.
 */

/** What the canvas shows. JSON-serialisable; never carries an unattempted question's answer fields. */
export interface CanvasRef {
  /** Unique per thread (server-assigned); opening an item with the same key replaces it. */
  key: string;
  /** Registered kind: "document" | "diagram" | "table" | "chart" | "video" | (S15) "pdf" | (S15b) "generated_media". */
  kind: string;
  /** Who wrote it: "assistant" renders violet; "student" renders paper (e.g. after Keep). */
  origin: "assistant" | "student";
  /** ui_copy key for the panel title, or null to use `title`. */
  title_key: string | null;
  /** Plain-text title chosen by the tool; rendered as text, never as HTML. */
  title: string | null;
  /** Server-side id of the thing shown, when it lives in a table (note id, media id, pdf upload id ...). */
  ref: { type: string; id: string } | null;
  /** Kind-specific payload, validated by the kind's schema before rendering. */
  data: Record<string, unknown>;
}

export const zCanvasRef = z.object({
  key: z.string().min(1).max(200),
  kind: z.string().min(1).max(64),
  origin: z.enum(["assistant", "student"]),
  title_key: z.string().max(200).nullable(),
  title: z.string().max(200).nullable(),
  ref: z.object({ type: z.string().min(1), id: z.string().min(1) }).nullable(),
  data: z.record(z.string(), z.unknown()),
}) satisfies ZodType<CanvasRef>;

export interface CanvasKindProps<D = Record<string, unknown>> {
  item: CanvasRef & { data: D };
  /** Close this item (the panel stays open if others remain). */
  close(): void;
  /** Replace this item in place (e.g. document "Keep" flips origin to "student" and sets ref). */
  update(next: Partial<CanvasRef>): void;
}

export interface CanvasKind<D = Record<string, unknown>> {
  kind: string;
  /** Validates `item.data`; an item that fails renders the shared error state (canvas.invalid), never crashes. */
  schema: ZodType<D>;
  /** Lazy component; loaded only when an item of this kind opens. */
  load(): Promise<{ default: ComponentType<CanvasKindProps<D>> }>;
  /** ui_copy key used when the item has neither title_key nor title (e.g. "canvas.kind.pdf"). */
  label_key: string;
  /** Lucide icon name for the tab chip. */
  icon: string;
  /** Feature flag that must be on (the signed-in user's features) or the kind is ignored. */
  requires_flag?: string;
}

// ── kinds ──

const kinds = new Map<string, CanvasKind>();
const kindListeners = new Set<() => void>();
let kindsVersion = 0;

function kindsChanged(): void {
  kindsVersion += 1;
  for (const l of kindListeners) l();
}

/** Register a kind; returns unregister. Same `kind` twice → the later registration wins (HMR-safe). */
export function registerCanvasKind<D>(spec: CanvasKind<D>): () => void {
  const entry = spec as unknown as CanvasKind;
  kinds.set(spec.kind, entry);
  kindsChanged();
  return () => {
    if (kinds.get(spec.kind) === entry) {
      kinds.delete(spec.kind);
      kindsChanged();
    }
  };
}

export function getCanvasKind(kind: string): CanvasKind | undefined {
  return kinds.get(kind);
}

/** Re-render when kinds register or unregister (a lazily registered S15 kind shows up without a reload). */
export function useCanvasKindsVersion(): number {
  return useSyncExternalStore(
    (l) => {
      kindListeners.add(l);
      return () => kindListeners.delete(l);
    },
    () => kindsVersion,
    () => kindsVersion,
  );
}

/** A kind usable for this user: registered, and its flag (if any) is on. */
export function usableKind(kind: string, features: Readonly<Record<string, boolean>>): CanvasKind | undefined {
  const k = kinds.get(kind);
  if (!k) return undefined;
  if (k.requires_flag && features[k.requires_flag] !== true) return undefined;
  return k;
}

// ── open items ──

interface CanvasState {
  items: CanvasRef[];
  active: string | null;
}

let state: CanvasState = { items: [], active: null };
const listeners = new Set<() => void>();

function setState(next: CanvasState): void {
  state = next;
  for (const l of listeners) l();
}

/** Open / focus an item (from a block, a button or a page). An item with the same key is replaced in place. */
export function openInCanvas(ref: CanvasRef): void {
  const i = state.items.findIndex((x) => x.key === ref.key);
  const items = i >= 0 ? state.items.map((x, j) => (j === i ? ref : x)) : [...state.items, ref];
  setState({ items, active: ref.key });
}

export function closeCanvasItem(key: string): void {
  const i = state.items.findIndex((x) => x.key === key);
  if (i < 0) return;
  const items = state.items.filter((x) => x.key !== key);
  const active = state.active === key ? (items[Math.min(i, items.length - 1)]?.key ?? null) : (state.active ?? null);
  setState({ items, active });
}

/** Close every item (the panel goes). */
export function closeCanvas(): void {
  if (state.items.length === 0) return;
  setState({ items: [], active: null });
}

export function updateCanvasItem(key: string, next: Partial<CanvasRef>): void {
  const i = state.items.findIndex((x) => x.key === key);
  if (i < 0) return;
  const merged = { ...state.items[i], ...next, key } as CanvasRef;
  setState({ ...state, items: state.items.map((x, j) => (j === i ? merged : x)) });
}

export function focusCanvasItem(key: string): void {
  if (state.active === key || !state.items.some((x) => x.key === key)) return;
  setState({ ...state, active: key });
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

const snapshot = () => state;

export function useCanvas(): {
  items: CanvasRef[];
  active: string | null;
  open: (ref: CanvasRef) => void;
  close: (key?: string) => void;
  update: (key: string, next: Partial<CanvasRef>) => void;
  focus: (key: string) => void;
} {
  const s = useSyncExternalStore(subscribe, snapshot, snapshot);
  return {
    items: s.items,
    active: s.active,
    open: openInCanvas,
    close: (key?: string) => (key === undefined ? closeCanvas() : closeCanvasItem(key)),
    update: updateCanvasItem,
    focus: focusCanvasItem,
  };
}

// ── Keep (document → the student's note) ──

/**
 * Saves a canvas document as the student's own note. Registered by whoever owns the notes API (S13, or the S14
 * owner's wiring); the document kind shows "Keep" only while a handler is registered, so it never does nothing.
 * Resolves with the stored note's ref; the item then turns paper (origin "student").
 */
export type CanvasKeepHandler = (item: CanvasRef) => Promise<{ type: string; id: string }>;

let keepHandler: CanvasKeepHandler | null = null;
const keepListeners = new Set<() => void>();

export function registerCanvasKeep(handler: CanvasKeepHandler): () => void {
  keepHandler = handler;
  for (const l of keepListeners) l();
  return () => {
    if (keepHandler === handler) {
      keepHandler = null;
      for (const l of keepListeners) l();
    }
  };
}

export function useCanvasKeep(): CanvasKeepHandler | null {
  return useSyncExternalStore(
    (l) => {
      keepListeners.add(l);
      return () => keepListeners.delete(l);
    },
    () => keepHandler,
    () => null,
  );
}

/** Tests only: reset the open items. */
export function resetCanvasForTests(): void {
  setState({ items: [], active: null });
}
