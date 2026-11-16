import { WifiOff } from "lucide-react";
import { cx } from "../cx";

export interface OfflineBannerProps {
  message: string;
  className?: string;
  /** false for static specimens. */
  live?: boolean;
}

/** Shown while navigator.onLine is false. The word "Offline" carries the status, not the colour. */
export function OfflineBanner({ message, className, live = true }: OfflineBannerProps) {
  return (
    <div
      role={live ? "status" : undefined}
      aria-live={live ? "polite" : undefined}
      className={cx(
        "flex items-center gap-3 rounded-pill border border-line-strong bg-surface-2 px-4 py-2.5 text-text",
        className,
      )}
    >
      <WifiOff aria-hidden="true" size={18} className="shrink-0 text-text-2" />
      <span className="font-semibold">Offline</span>
      <span className="text-text-2">{message}</span>
    </div>
  );
}
