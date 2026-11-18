import { cx } from "../cx";

export interface SkeletonProps {
  className?: string;
}

/** Loading placeholder. Decorative: the surrounding region carries aria-busy and a text status. */
export function Skeleton({ className }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      data-skeleton=""
      className={cx(
        "block animate-shimmer rounded-inner bg-[length:200%_100%]",
        "bg-[linear-gradient(90deg,var(--color-surface-2)_25%,var(--color-line-strong)_50%,var(--color-surface-2)_75%)]",
        className,
      )}
    />
  );
}
