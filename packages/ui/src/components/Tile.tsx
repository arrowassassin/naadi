import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";

export type TileVariant = "default" | "assistant" | "paper";

export interface TileProps extends HTMLAttributes<HTMLElement> {
  /** default = surface 1 · assistant = violet ground (AI-written only) · paper = the student's own work */
  variant?: TileVariant;
  as?: "div" | "section" | "article" | "li" | "aside";
  children: ReactNode;
}

const VARIANTS: Record<TileVariant, string> = {
  default: "bg-surface-1 border border-line-soft text-text",
  assistant: "bg-violet-ground border border-violet-line text-text",
  paper: "bg-paper border border-paper text-ground",
};

export function Tile({ variant = "default", as: As = "div", className, children, ...rest }: TileProps) {
  return (
    <As
      data-variant={variant}
      className={cx("rounded-tile-sm tablet:rounded-tile p-6 tablet:p-7", VARIANTS[variant], className)}
      {...rest}
    >
      {children}
    </As>
  );
}

export interface TileLabelProps {
  children: ReactNode;
  variant?: TileVariant;
  className?: string;
}

/** The mono uppercase label at the top of a tile; its colour follows the tile's role. */
export function TileLabel({ children, variant = "default", className }: TileLabelProps) {
  const tone = variant === "assistant" ? "text-violet-text" : variant === "paper" ? "text-paper-text-2" : "text-text-3";
  return <span className={cx("font-mono text-label uppercase tracking-[0.12em]", tone, className)}>{children}</span>;
}
