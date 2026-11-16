import type { EraserMode, InkEngineState, InkTheme, InkToolName } from "@naadi/ink";
import type { InkColor } from "@naadi/shared";
import { Button, cx } from "@naadi/ui";
import { Redo2, Trash2, Undo2 } from "lucide-react";
import { useRef } from "react";

export const TOOLS: { tool: InkToolName; label: string; key?: string }[] = [
  { tool: "pen", label: "Pen", key: "P" },
  { tool: "marker", label: "Marker", key: "M" },
  { tool: "eraser", label: "Eraser", key: "E" },
  { tool: "lasso", label: "Lasso", key: "L" },
  { tool: "shapes", label: "Shapes" },
  { tool: "text", label: "Text" },
];

/** Swatch names and the token class that shows each colour role on the given theme. */
const PEN_COLOURS: { color: InkColor; label: Record<InkTheme, string>; swatch: Record<InkTheme, string> }[] = [
  { color: "ink", label: { paper: "Ink black", dark: "Ink white" }, swatch: { paper: "bg-ground", dark: "bg-paper" } },
  {
    color: "violet",
    label: { paper: "Ink violet", dark: "Ink violet" },
    swatch: { paper: "bg-ink-violet", dark: "bg-violet-text" },
  },
];
const MARKER_COLOURS: { color: InkColor; label: string; swatch: Record<InkTheme, string> }[] = [
  { color: "lime", label: "Marker lime, key fact", swatch: { paper: "bg-lime", dark: "bg-lime" } },
  { color: "amber", label: "Marker amber, my mistake", swatch: { paper: "bg-mark-amber", dark: "bg-amber" } },
];

const toolBtn =
  "inline-flex min-h-11 items-center justify-center rounded-pill px-3 text-[0.8125rem] leading-none select-none transition-colors duration-(--duration-control) ease-out";

export interface InkToolbarProps {
  state: InkEngineState;
  theme: InkTheme;
  onEnabled: (on: boolean) => void;
  onTool: (tool: InkToolName) => void;
  onColor: (color: InkColor) => void;
  onEraserMode: (mode: EraserMode) => void;
  onUndo: () => void;
  onRedo: () => void;
  /** Called after the student confirmed. */
  onClear: () => void;
  className?: string;
}

/** Drawing toolbar per the Chapter and Ink boards: 44 px targets, accessible names, shortcuts announced. */
export function InkToolbar({
  state,
  theme,
  onEnabled,
  onTool,
  onColor,
  onEraserMode,
  onUndo,
  onRedo,
  onClear,
  className,
}: InkToolbarProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const markerish = state.tool === "marker";
  const colourless = state.tool === "eraser" || state.tool === "lasso";
  return (
    <div
      role="toolbar"
      aria-label="Ink tools"
      className={cx(
        // One row at every width; on a phone it scrolls sideways instead of wrapping into three rows.
        "relative flex flex-nowrap items-center gap-0.5 overflow-x-auto overscroll-x-contain rounded-[28px] border border-line-strong bg-ground p-1.5 [scrollbar-width:none] [&>*]:shrink-0",
        className,
      )}
    >
      <button
        type="button"
        aria-pressed={state.enabled}
        onClick={() => onEnabled(!state.enabled)}
        // The one lime control on the page: turning ink on/off is the student's next action (Ink board).
        className={cx(toolBtn, "bg-lime px-4 font-semibold text-lime-ink hover:bg-lime-hover")}
      >
        {state.enabled ? "Ink on" : "Ink off"}
      </button>
      <span aria-hidden="true" className="mx-0.5 h-6 w-px bg-line-strong" />
      {TOOLS.map((t) => {
        const on = state.tool === t.tool;
        return (
          <button
            key={t.tool}
            type="button"
            aria-pressed={on}
            aria-keyshortcuts={t.key}
            title={t.key ? `${t.label} (${t.key})` : t.label}
            disabled={!state.enabled}
            onClick={() => onTool(t.tool)}
            className={cx(
              toolBtn,
              on && state.enabled ? "bg-paper font-semibold text-ground" : "text-text hover:bg-surface-2",
              !state.enabled && "cursor-not-allowed text-text-muted",
            )}
          >
            {t.label}
          </button>
        );
      })}
      <span aria-hidden="true" className="mx-0.5 h-6 w-px bg-line-strong" />
      {state.tool === "eraser" ? (
        <fieldset className="m-0 flex items-center gap-1 border-0 p-0">
          <legend className="sr-only">Eraser mode</legend>
          {(
            [
              ["stroke", "Whole stroke"],
              ["pixel", "Partial"],
            ] as const
          ).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              aria-pressed={state.eraserMode === mode}
              disabled={!state.enabled}
              onClick={() => onEraserMode(mode)}
              className={cx(
                toolBtn,
                state.eraserMode === mode
                  ? "border border-paper font-semibold text-text"
                  : "text-text-2 hover:bg-surface-2",
              )}
            >
              {label}
            </button>
          ))}
        </fieldset>
      ) : (
        <fieldset className="m-0 flex items-center gap-1 border-0 p-0">
          <legend className="sr-only">{markerish ? "Marker colour" : "Ink colour"}</legend>
          {(markerish ? MARKER_COLOURS : PEN_COLOURS).map((c) => {
            const checked = state.color === c.color;
            const label = typeof c.label === "string" ? c.label : c.label[theme];
            return (
              <button
                key={c.color}
                type="button"
                aria-pressed={checked}
                aria-label={label}
                title={label}
                disabled={!state.enabled || colourless}
                onClick={() => onColor(c.color)}
                className={cx(
                  "size-11 rounded-full border-[3px]",
                  c.swatch[theme],
                  // Unselected swatches keep a visible ring (WCAG 1.4.11): the black one sits on a black bar.
                  checked ? "border-paper outline-2 outline-offset-1 outline-ground" : "border-text-3",
                  (!state.enabled || colourless) && "cursor-not-allowed opacity-50",
                )}
              />
            );
          })}
        </fieldset>
      )}
      <span className="flex-1" />
      <button
        type="button"
        aria-label="Undo"
        aria-keyshortcuts="Meta+Z Control+Z"
        title="Undo (⌘Z)"
        disabled={!state.canUndo}
        onClick={onUndo}
        className={cx(
          "inline-flex size-11 items-center justify-center rounded-full border border-line-control",
          state.canUndo ? "text-text hover:bg-surface-2" : "cursor-not-allowed text-text-muted",
        )}
      >
        <Undo2 aria-hidden="true" size={18} />
      </button>
      <button
        type="button"
        aria-label="Redo"
        aria-keyshortcuts="Shift+Meta+Z Shift+Control+Z"
        title="Redo (⇧⌘Z)"
        disabled={!state.canRedo}
        onClick={onRedo}
        className={cx(
          "inline-flex size-11 items-center justify-center rounded-full border border-line-control",
          state.canRedo ? "text-text hover:bg-surface-2" : "cursor-not-allowed text-text-muted",
        )}
      >
        <Redo2 aria-hidden="true" size={18} />
      </button>
      <button
        type="button"
        aria-label="Clear page"
        title="Clear page"
        disabled={state.count === 0}
        onClick={() => dialog.current?.showModal()}
        className={cx(
          "inline-flex size-11 items-center justify-center rounded-full border border-line-control",
          state.count > 0 ? "text-text hover:bg-surface-2" : "cursor-not-allowed text-text-muted",
        )}
      >
        <Trash2 aria-hidden="true" size={18} />
      </button>
      <dialog
        ref={dialog}
        aria-labelledby="ink-clear-h"
        aria-describedby="ink-clear-d"
        className="m-auto max-w-[min(420px,calc(100vw-32px))] rounded-block border border-line-strong bg-surface-1 p-6 text-text backdrop:bg-ground/70"
      >
        <h2 id="ink-clear-h" className="m-0 font-display text-[1.375rem] font-bold">
          Clear all ink on this page?
        </h2>
        <p id="ink-clear-d" className="mt-2 mb-5 text-text-2">
          Every stroke and text box on this page is removed. Undo brings them back.
        </p>
        <form method="dialog" className="flex flex-wrap justify-end gap-2">
          <Button type="submit" variant="secondary" autoFocus>
            Cancel
          </Button>
          <Button
            type="button"
            variant="strong"
            onClick={() => {
              dialog.current?.close();
              onClear();
            }}
          >
            Clear page
          </Button>
        </form>
      </dialog>
    </div>
  );
}
