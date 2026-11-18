import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cx } from "../cx";

export type ButtonVariant = "primary" | "strong" | "secondary" | "assistant";
export type ButtonSize = "default" | "cta";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = lime (the one next action per view) · strong = paper · secondary = outline · assistant = violet */
  variant?: ButtonVariant;
  /** default is 48 px tall; cta is 56 px (primary CTAs are 52–60 px) */
  size?: ButtonSize;
  icon?: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-lime text-lime-ink font-semibold hover:bg-lime-hover border border-transparent",
  strong: "bg-paper text-ground font-semibold hover:bg-white border border-transparent",
  secondary: "bg-transparent text-text border border-line-control hover:border-text-3 hover:bg-surface-2",
  assistant: "bg-violet text-violet-ink font-semibold hover:bg-violet-text border border-transparent",
};

const DISABLED = "bg-transparent text-text-muted border border-dashed border-line-dashed cursor-not-allowed";

export function Button({
  variant = "secondary",
  size = "default",
  icon,
  disabled,
  className,
  children,
  type = "button",
  ref,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled}
      data-variant={variant}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-pill leading-none select-none",
        "transition-colors duration-(--duration-control) ease-out",
        size === "cta" ? "min-h-14 px-7 text-[1rem]" : "min-h-12 px-5 text-[0.875rem]",
        disabled ? DISABLED : VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {icon ? (
        <span aria-hidden="true" className="inline-flex shrink-0">
          {icon}
        </span>
      ) : null}
      {children}
    </button>
  );
}
