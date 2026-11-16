import type { ReactNode } from "react";
import { cx } from "../cx";

export type HighlightKind = "key-fact" | "formula" | "mistake";

/** Fixed meanings, so Notes can filter by colour: lime = key fact, violet = formula, amber = my mistake. */
export const HIGHLIGHT_LABEL: Record<HighlightKind, string> = {
  "key-fact": "Key fact",
  formula: "Formula",
  mistake: "My mistake",
};

const KIND_CLASS: Record<HighlightKind, string> = {
  "key-fact": "bg-lime",
  formula: "bg-violet-text",
  mistake: "bg-mark-amber",
};

export interface HighlightProps {
  kind: HighlightKind;
  children: ReactNode;
  className?: string;
}

export function Highlight({ kind, children, className }: HighlightProps) {
  return (
    <mark data-kind={kind} className={cx("rounded-[4px] px-1 py-px text-ground", KIND_CLASS[kind], className)}>
      <span className="sr-only">{HIGHLIGHT_LABEL[kind]}: </span>
      {children}
    </mark>
  );
}
