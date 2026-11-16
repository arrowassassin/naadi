import { cx } from "@naadi/ui";

/** The Naadi mark (pulse line on a lime disc) and wordmark, as on the Nav and Onboarding boards. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="flex shrink-0 items-center justify-center rounded-pill bg-lime"
      style={{ width: size, height: size }}
    >
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M2 12h4l3-7 4 14 3-7h6"
          className="stroke-lime-ink"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export function Wordmark({ className, size = 28 }: { className?: string; size?: number }) {
  return (
    <span className={cx("flex items-center gap-2 font-bold font-display text-text", className)}>
      <LogoMark size={size} />
      Naadi
    </span>
  );
}
