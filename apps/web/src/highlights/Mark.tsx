import { cx } from "@naadi/ui";
import { type ReactNode, useCallback } from "react";
import type { TextDecorator } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { type HighlightColor, type PaintRange, splitRun } from "./anchor";

/**
 * The three highlight colours have fixed meanings (HANDOFF §4): lime = key fact, violet = formula, amber = my
 * mistake. Colour is never the only signal: every mark starts with its word for screen readers, and each colour has
 * its own line style (key fact solid, formula dotted underline, mistake wavy underline), so the three differ in
 * greyscale and for colour-blind readers too. Lists show the word visibly (HighlightWord).
 */
export const MARK_CLASS: Record<HighlightColor, string> = {
  lime: "bg-lime",
  violet: "bg-violet-text underline decoration-dotted decoration-2 underline-offset-[3px]",
  amber: "bg-mark-amber underline decoration-wavy decoration-1 underline-offset-[3px]",
};

/** The swatch/bar colour of a highlight (token utilities only). */
export const SWATCH_CLASS: Record<HighlightColor, string> = {
  lime: "bg-lime",
  violet: "bg-violet-text",
  amber: "bg-mark-amber",
};

export const WORD_KEY: Record<HighlightColor, string> = {
  lime: "highlights.word.lime",
  violet: "highlights.word.violet",
  amber: "highlights.word.amber",
};

export function HighlightMark({
  range,
  first,
  word,
  active,
  children,
}: {
  range: PaintRange;
  first: boolean;
  word: string;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <mark
      data-color={range.color}
      data-highlight-id={range.id}
      data-active={active || undefined}
      className={cx(
        "rounded-[4px] px-0.5 py-px text-ground decoration-ground [box-decoration-break:clone]",
        MARK_CLASS[range.color],
        active && "outline-2 outline-text outline-offset-1",
      )}
    >
      {first ? (
        <span data-sr-word="" className="sr-only">
          {`${word}: `}
        </span>
      ) : null}
      {children}
    </mark>
  );
}

/**
 * The decorator a highlightable surface passes to its renderer: every text node becomes `<span data-run=pos>`
 * (the selection anchors, see dom.ts) with the located highlights painted as marks. Stable for the same ranges.
 */
export function useHighlightDecorator(ranges: readonly PaintRange[], activeId: string | null): TextDecorator {
  const t = useUiText();
  const lime = t(WORD_KEY.lime);
  const violet = t(WORD_KEY.violet);
  const amber = t(WORD_KEY.amber);
  return useCallback<TextDecorator>(
    (text, pos, key) => {
      const words: Record<HighlightColor, string> = { lime, violet, amber };
      const pieces = splitRun(text, pos, ranges);
      let at = pos;
      return (
        <span key={key} data-run={pos}>
          {pieces.map((p) => {
            const k = at;
            at += p.text.length;
            return p.range ? (
              <HighlightMark
                key={k}
                range={p.range}
                first={p.first}
                word={words[p.range.color]}
                active={p.range.id === activeId}
              >
                {p.text}
              </HighlightMark>
            ) : (
              p.text
            );
          })}
        </span>
      );
    },
    [ranges, activeId, lime, violet, amber],
  );
}

/** The visible word of a colour with its swatch (lists, chips, filters): status is never colour alone. */
export function HighlightWord({ color, className }: { color: HighlightColor; className?: string }) {
  const t = useUiText();
  return (
    <span className={cx("inline-flex items-center gap-1.5", className)}>
      <span aria-hidden="true" className={cx("inline-block size-3 shrink-0 rounded-full", SWATCH_CLASS[color])} />
      {t(WORD_KEY[color])}
    </span>
  );
}
