import type { ReactNode } from "react";
import { cx } from "../cx";

export type ChipTone = "now" | "current" | "done" | "planned" | "weak" | "meta" | "warning" | "critical" | "assistant";

export interface ChipProps {
  /** The word is mandatory: status never relies on colour alone. */
  children: string;
  tone?: ChipTone;
  icon?: ReactNode;
  className?: string;
  /** Visually hidden prefix for assistive tech, e.g. "Database status" → "Database status: Down". */
  srPrefix?: string;
}

const TONES: Record<ChipTone, string> = {
  now: "bg-lime text-lime-ink font-semibold",
  /** Neutral outline for "this one is current" where the view's single lime is already the next action. */
  current: "border border-text text-text font-semibold",
  done: "bg-surface-2 text-text",
  planned: "border border-dashed border-line-dashed text-text-2",
  weak: "border border-amber text-amber",
  meta: "border border-line-chip text-text-2",
  warning: "border border-amber text-amber",
  critical: "bg-amber text-ground font-semibold",
  assistant: "bg-violet-ground border border-violet-line text-violet-text",
};

export function Chip({ children, tone = "done", icon, className, srPrefix }: ChipProps) {
  return (
    <span
      data-tone={tone}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-pill py-[5px] leading-[1.2] whitespace-nowrap",
        tone === "meta" ? "px-2.5 font-mono text-[0.6875rem]" : "px-3 text-[0.75rem]",
        TONES[tone],
        className,
      )}
    >
      {icon ? (
        <span aria-hidden="true" className="inline-flex shrink-0">
          {icon}
        </span>
      ) : null}
      {srPrefix ? <span className="sr-only">{srPrefix}: </span> : null}
      {children}
    </span>
  );
}
