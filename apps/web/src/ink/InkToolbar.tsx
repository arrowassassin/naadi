import type { EraserMode, InkEngineState, InkTheme, InkToolName } from "@naadi/ink";
import type { InkColor } from "@naadi/shared";
import { Button, cx } from "@naadi/ui";
import { Eraser, Highlighter, Lasso, type LucideIcon, Pen, Redo2, Shapes, Trash2, Type, Undo2 } from "lucide-react";
import { useRef } from "react";
import { useInkCopy } from "./copy";

/** Tools with their keyboard shortcut (the letter is a key, not copy: it is what the student presses) and the icon
 * a phone shows in place of the word. */
export const TOOLS: { tool: InkToolName; key?: string; icon: LucideIcon }[] = [
  { tool: "pen", key: "P", icon: Pen },
  { tool: "marker", key: "M", icon: Highlighter },
  { tool: "eraser", key: "E", icon: Eraser },
  { tool: "lasso", key: "L", icon: Lasso },
  { tool: "shapes", icon: Shapes },
  { tool: "text", icon: Type },
];

/** The ui_copy key naming each swatch and the token class that shows each colour role on the given theme. */
const PEN_COLOURS: { color: InkColor; label: Record<InkTheme, string>; swatch: Record<InkTheme, string> }[] = [
  {
    color: "ink",
    label: { paper: "ink.colour.ink_paper", dark: "ink.colour.ink_dark" },
    swatch: { paper: "bg-ground", dark: "bg-paper" },
  },
  {
    color: "violet",
    label: { paper: "ink.colour.violet", dark: "ink.colour.violet" },
    swatch: { paper: "bg-ink-violet", dark: "bg-violet-text" },
  },
];
const MARKER_COLOURS: { color: InkColor; label: string; swatch: Record<InkTheme, string> }[] = [
  { color: "lime", label: "ink.colour.lime", swatch: { paper: "bg-lime", dark: "bg-lime" } },
  { color: "amber", label: "ink.colour.amber", swatch: { paper: "bg-mark-amber", dark: "bg-amber" } },
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

/**
 * Drawing toolbar per the Chapter and Ink boards: 44 px targets, accessible names, shortcuts announced.
 *
 * Phone widths (under 640 px): tools are 44 px icons, with the name kept as screen-reader text and as the title. The
 * bar wraps onto a second row (colours, undo, redo, clear), so every control is on screen. Nothing scrolls sideways
 * out of sight.
 */
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
  const t = useInkCopy();
  const markerish = state.tool === "marker";
  const colourless = state.tool === "eraser" || state.tool === "lasso";
  return (
    <div
      role="toolbar"
      aria-label={t("ink.toolbar")}
      className={cx(
        // One row from 640 px up; on a phone, icons and a second row (never a sideways scroll that hides controls).
        "relative flex flex-nowrap items-center gap-0.5 rounded-[28px] border border-line-strong bg-ground p-1.5 max-tablet:flex-wrap max-tablet:gap-y-1 [&>*]:shrink-0",
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
        {t(state.enabled ? "ink.toggle_on" : "ink.toggle_off")}
      </button>
      <span aria-hidden="true" className="mx-0.5 h-6 w-px bg-line-strong" />
      {TOOLS.map((tool) => {
        const on = state.tool === tool.tool;
        const label = t(`ink.tool.${tool.tool}`);
        const Icon = tool.icon;
        return (
          <button
            key={tool.tool}
            type="button"
            aria-pressed={on}
            aria-keyshortcuts={tool.key}
            title={tool.key ? t("ink.tool.with_key", { tool: label, key: tool.key }) : label}
            disabled={!state.enabled}
            onClick={() => onTool(tool.tool)}
            className={cx(
              toolBtn,
              "max-tablet:size-11 max-tablet:px-0",
              on && state.enabled ? "bg-paper font-semibold text-ground" : "text-text hover:bg-surface-2",
              !state.enabled && "cursor-not-allowed text-text-muted",
            )}
          >
            <Icon aria-hidden="true" size={20} className="tablet:hidden" />
            <span className="max-tablet:sr-only">{label}</span>
          </button>
        );
      })}
      <span aria-hidden="true" className="mx-0.5 h-6 w-px bg-line-strong max-tablet:hidden" />
      {state.tool === "eraser" ? (
        <fieldset className="m-0 flex items-center gap-1 border-0 p-0">
          <legend className="sr-only">{t("ink.eraser.mode")}</legend>
          {(
            [
              ["stroke", "ink.eraser.stroke"],
              ["pixel", "ink.eraser.pixel"],
            ] as const
          ).map(([mode, labelKey]) => (
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
              {t(labelKey)}
            </button>
          ))}
        </fieldset>
      ) : (
        <fieldset className="m-0 flex items-center gap-1 border-0 p-0">
          <legend className="sr-only">{t(markerish ? "ink.colour.marker_group" : "ink.colour.ink_group")}</legend>
          {(markerish ? MARKER_COLOURS : PEN_COLOURS).map((c) => {
            const checked = state.color === c.color;
            const label = t(typeof c.label === "string" ? c.label : c.label[theme]);
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
        aria-label={t("ink.undo")}
        aria-keyshortcuts="Meta+Z Control+Z"
        title={t("ink.undo_hint")}
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
        aria-label={t("ink.redo")}
        aria-keyshortcuts="Shift+Meta+Z Shift+Control+Z"
        title={t("ink.redo_hint")}
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
        aria-label={t("ink.clear")}
        title={t("ink.clear")}
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
          {t("ink.clear_title")}
        </h2>
        <p id="ink-clear-d" className="mt-2 mb-5 text-text-2">
          {t("ink.clear_body")}
        </p>
        <form method="dialog" className="flex flex-wrap justify-end gap-2">
          <Button type="submit" variant="secondary" autoFocus>
            {t("ink.clear_cancel")}
          </Button>
          <Button
            type="button"
            variant="strong"
            onClick={() => {
              dialog.current?.close();
              onClear();
            }}
          >
            {t("ink.clear_confirm")}
          </Button>
        </form>
      </dialog>
    </div>
  );
}

const STICKY = {
  top: "sticky top-[calc(var(--shell-top,0px)+8px)] z-10",
  bottom: "sticky bottom-[calc(var(--shell-bottom,0px)+12px)] z-10",
} as const;

/**
 * The ink toolbar pinned to the top or bottom of the scrolling page: use this instead of a sticky wrapper
 * of your own. The wrapper gets its own compositing layer (will-change: transform). Without one, WebKit
 * repaints the whole scrolled page, ink pictures included, on every scroll frame to keep the bar in place;
 * on webkit-laptop-1440 that cost the 5,000-stroke page 20-40 % of its frame rate. The clear dialog is
 * modal (top layer), so the containing block will-change creates does not affect it.
 */
export function StickyInkToolbar({
  placement,
  wrapperClassName,
  ...toolbar
}: InkToolbarProps & { placement: keyof typeof STICKY; wrapperClassName?: string }) {
  return (
    <div className={cx(STICKY[placement], "will-change-transform", wrapperClassName)} data-sticky-ink-toolbar="">
      <InkToolbar {...toolbar} />
    </div>
  );
}
