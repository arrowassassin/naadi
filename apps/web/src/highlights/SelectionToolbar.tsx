import { cx } from "@naadi/ui";
import { Check, Layers, MessageCircleQuestion, NotebookPen, Trash2 } from "lucide-react";
import {
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useUiText } from "../copy/useUiText";
import { type AnchorPos, HIGHLIGHT_COLORS, type HighlightColor, makeAnchor, type TextRun } from "./anchor";
import { isTypingTarget, rangeToPositions, selectionRangeIn } from "./dom";
import { SWATCH_CLASS, WORD_KEY } from "./Mark";

export interface SelectionInfo {
  anchor: AnchorPos;
}

export interface ToolbarActions {
  onHighlight: (color: HighlightColor, sel: SelectionInfo) => void;
  onNote: (sel: SelectionInfo) => void;
  /** Make a flashcard from the selection (section selections only); absent = no Card button. */
  onCard?: (sel: SelectionInfo) => void;
  /** Ask the assistant about the selection; absent (flag off, no assistant, exam mode) = no Ask button. */
  onAsk?: (sel: SelectionInfo) => void;
  /** An existing highlight was tapped: change its colour or remove it. */
  onRecolor: (id: string, color: HighlightColor) => void;
  onRemove: (id: string) => void;
  /** The colour of an existing highlight (null when it is not one of the student's). */
  colorOf: (id: string) => HighlightColor | null;
}

type Mode =
  | { kind: "selection"; sel: SelectionInfo; top: number; left: number; below: boolean; w: number }
  | { kind: "highlight"; id: string; top: number; left: number; below: boolean };

/** Debounce for `selectionchange` (iOS fires it for every handle move). */
const SETTLE_MS = 160;
const GAP = 10;

function coarsePointer(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(pointer: coarse)").matches
    : false;
}

/** The selection's first line (fine pointer) or last line (touch), or its box; its element's box as a last resort. */
function selectionRect(range: Range): DOMRect {
  if (typeof range.getClientRects === "function") {
    const rects = range.getClientRects();
    const r = coarsePointer() ? rects[rects.length - 1] : rects[0];
    if (r) return r;
    return range.getBoundingClientRect();
  }
  const node = range.commonAncestorContainer;
  const el = node instanceof Element ? node : node.parentElement;
  return el?.getBoundingClientRect() ?? new DOMRect();
}

/**
 * Where the toolbar goes, relative to `host`: above the selection with a fine pointer, below it on touch (iOS draws
 * its own Copy / Look Up menu above a selection), flipped when there is no room, kept inside the host.
 */
function place(rect: DOMRect, host: HTMLElement, width: number, height: number) {
  const h = host.getBoundingClientRect();
  const viewTop = Math.max(0, -h.top);
  let below = coarsePointer();
  if (!below && rect.top - h.top - height - GAP < viewTop) below = true;
  if (below && rect.bottom + height + GAP > window.innerHeight && rect.top - height - GAP > 0) below = false;
  const top = below ? rect.bottom - h.top + GAP : rect.top - h.top - height - GAP;
  const centre = rect.left - h.left + rect.width / 2;
  const left = Math.min(Math.max(0, centre - width / 2), Math.max(0, h.width - width));
  return { top: Math.max(0, top), left, below };
}

/**
 * The selection toolbar (segment 13) for one surface: Highlight (three swatches, each with its word), Note, Card
 * and Ask. It opens when the student selects text in `surfaceRef` (mouse, keyboard, touch or Pencil: it follows
 * `selectionchange`), and when they tap one of their highlights (change colour, remove). Tab from the selection
 * moves into it, arrow keys move between its buttons, Escape closes it. It never reacts inside a field.
 * Positioned inside `hostRef` (position: relative), so it scrolls with the text and stays in the page's landmarks.
 */
export function SelectionToolbar({
  surfaceRef,
  hostRef,
  runs,
  actions,
  label,
  enabled = true,
}: {
  surfaceRef: RefObject<HTMLElement | null>;
  hostRef: RefObject<HTMLElement | null>;
  runs: readonly TextRun[];
  actions: ToolbarActions;
  label?: string;
  /** Off while the surface takes ink (Pencil draws instead of selecting). */
  enabled?: boolean;
}) {
  const t = useUiText();
  const [mode, setMode] = useState<Mode | null>(null);
  const bar = useRef<HTMLDivElement>(null);
  const interacting = useRef(false);
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const actionsRef = useRef(actions);
  actionsRef.current = actions;

  const measure = useCallback(() => ({ w: bar.current?.offsetWidth || 320, h: bar.current?.offsetHeight || 56 }), []);

  const readSelection = useCallback(() => {
    const surface = surfaceRef.current;
    const host = hostRef.current;
    const range = selectionRangeIn(surface);
    if (!surface || !host || !range) {
      if (!interacting.current && modeRef.current?.kind === "selection") setMode(null);
      return;
    }
    const pos = rangeToPositions(surface, range);
    const anchor = pos ? makeAnchor(runsRef.current, pos.from, pos.to) : null;
    if (!anchor) {
      if (!interacting.current) setMode(null);
      return;
    }
    const { w, h } = measure();
    const rect = selectionRect(range);
    setMode({ kind: "selection", sel: { anchor }, ...place(rect, host, w, h), w });
  }, [surfaceRef, hostRef, measure]);

  useEffect(() => {
    if (!enabled) setMode(null);
  }, [enabled]);

  // Follow the selection (debounced), and tapping one of the student's highlights.
  useEffect(() => {
    if (!enabled) return;
    let timer = 0;
    const onSelection = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(readSelection, SETTLE_MS);
    };
    const surface = surfaceRef.current;
    const onClick = (e: MouseEvent) => {
      const mark = (e.target as Element | null)?.closest?.("mark[data-highlight-id]");
      const host = hostRef.current;
      if (!mark || !host || !(mark instanceof HTMLElement)) return;
      const sel = document.getSelection();
      if (sel && !sel.isCollapsed) return;
      const id = mark.dataset.highlightId as string;
      if (!actionsRef.current.colorOf(id)) return;
      const { w, h } = measure();
      setMode({ kind: "highlight", id, ...place(mark.getBoundingClientRect(), host, w, h) });
    };
    document.addEventListener("selectionchange", onSelection);
    surface?.addEventListener("click", onClick);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("selectionchange", onSelection);
      surface?.removeEventListener("click", onClick);
    };
  }, [enabled, readSelection, surfaceRef, hostRef, measure]);

  // Escape closes; Tab from the selection enters the toolbar; a press outside closes the highlight menu.
  useEffect(() => {
    if (!mode) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const inBar = bar.current?.contains(document.activeElement) ?? false;
      if (!inBar && isTypingTarget(e.target)) return;
      if (e.key === "Escape") {
        e.preventDefault();
        if (mode.kind === "selection") document.getSelection()?.removeAllRanges();
        setMode(null);
        if (inBar) surfaceRef.current?.closest<HTMLElement>("[tabindex]")?.focus();
        return;
      }
      if (e.key === "Tab" && !e.shiftKey && !inBar) {
        const active = document.activeElement;
        const control = active?.closest?.("a[href], button, input, select, textarea, [role=button]");
        if (control) return;
        e.preventDefault();
        bar.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    const onDown = (e: PointerEvent) => {
      if (bar.current?.contains(e.target as Node)) return;
      if (mode.kind === "highlight") setMode(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [mode, surfaceRef]);

  // Place again once the toolbar has its real size (the first placement used an estimate).
  const placedWidth = mode?.kind === "selection" ? mode.w : null;
  useLayoutEffect(() => {
    if (placedWidth !== null && bar.current && Math.abs(bar.current.offsetWidth - placedWidth) > 2) readSelection();
  }, [placedWidth, readSelection]);

  const done = (fn: () => void) => () => {
    interacting.current = false;
    fn();
    if (modeRef.current?.kind === "selection") document.getSelection()?.removeAllRanges();
    setMode(null);
  };

  const onBarKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    const buttons = [...(bar.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0 || buttons.length === 0) return;
    e.preventDefault();
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? buttons.length - 1
          : (at + (e.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  const current = mode?.kind === "highlight" ? actions.colorOf(mode.id) : null;
  const item = (key: string, onClick: () => void, children: ReactNode, extra?: string, pressed?: boolean) => (
    <button
      key={key}
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      data-action={key}
      className={cx(
        "flex min-h-11 shrink-0 items-center gap-2 rounded-[10px] border-0 bg-transparent px-3 text-[0.8125rem] text-text",
        "hover:bg-control focus-visible:outline-2 focus-visible:outline-text",
        extra,
      )}
    >
      {children}
    </button>
  );

  return (
    <>
      <p role="status" aria-live="polite" className="sr-only">
        {mode?.kind === "selection" ? t("highlights.toolbar_hint") : ""}
      </p>
      {mode ? (
        <div
          ref={bar}
          role="toolbar"
          aria-label={label ?? t("highlights.toolbar")}
          aria-orientation="horizontal"
          data-selection-toolbar={mode.kind}
          onKeyDown={onBarKey}
          onPointerDown={(e) => {
            interacting.current = true;
            // Keep the text selected while a button is pressed (mouse); touch keeps it by the grace flag above.
            if (e.pointerType === "mouse") e.preventDefault();
          }}
          onPointerUp={() => {
            window.setTimeout(() => {
              interacting.current = false;
            }, 400);
          }}
          onPointerCancel={() => {
            interacting.current = false;
          }}
          className="absolute z-30 flex max-w-full flex-wrap items-center gap-1 rounded-block-sm border border-line-strong bg-surface-2 p-1.5 shadow-[0_12px_32px_rgb(0_0_0/0.45)]"
          style={{ top: `${mode.top}px`, left: `${mode.left}px` }}
        >
          {HIGHLIGHT_COLORS.map((color) => {
            const on = current === color;
            return item(
              `highlight-${color}`,
              done(() => {
                if (mode.kind === "selection") actions.onHighlight(color, mode.sel);
                else if (!on) actions.onRecolor(mode.id, color);
              }),
              <>
                <span
                  aria-hidden="true"
                  className={cx(
                    "flex size-5 items-center justify-center rounded-full border border-ground/40",
                    SWATCH_CLASS[color],
                  )}
                >
                  {on ? <Check size={12} strokeWidth={3} className="text-ground" /> : null}
                </span>
                {t(WORD_KEY[color])}
              </>,
              undefined,
              mode.kind === "highlight" ? on : undefined,
            );
          })}
          <span aria-hidden="true" className="mx-0.5 h-6 w-px bg-line-strong" />
          {mode.kind === "selection" ? (
            <>
              {item(
                "note",
                done(() => actions.onNote(mode.sel)),
                <>
                  <NotebookPen aria-hidden="true" size={16} />
                  {t("highlights.action_note")}
                </>,
              )}
              {actions.onCard
                ? item(
                    "card",
                    done(() => actions.onCard?.(mode.sel)),
                    <>
                      <Layers aria-hidden="true" size={16} />
                      {t("highlights.action_card")}
                    </>,
                  )
                : null}
              {actions.onAsk
                ? item(
                    "ask",
                    done(() => actions.onAsk?.(mode.sel)),
                    <>
                      <MessageCircleQuestion aria-hidden="true" size={16} />
                      {t("highlights.action_ask")}
                    </>,
                    "text-violet-text",
                  )
                : null}
            </>
          ) : (
            item(
              "remove",
              done(() => actions.onRemove(mode.id)),
              <>
                <Trash2 aria-hidden="true" size={16} />
                {t("highlights.action_remove")}
              </>,
            )
          )}
        </div>
      ) : null}
    </>
  );
}
