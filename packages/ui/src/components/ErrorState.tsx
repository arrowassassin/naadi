import { AlertTriangle, RotateCw } from "lucide-react";
import { cx } from "../cx";
import { Button } from "./Button";

export interface ErrorStateProps {
  title: string;
  message: string;
  /** Shown so a report can be matched to the server log line. */
  requestId?: string | null;
  onRetry?: () => void;
  retryLabel?: string;
  retrying?: boolean;
  className?: string;
  /** false for static specimens: no role="alert", so nothing is announced on page load. */
  live?: boolean;
}

export function ErrorState({
  title,
  message,
  requestId,
  onRetry,
  retryLabel = "Try again",
  retrying = false,
  className,
  live = true,
}: ErrorStateProps) {
  return (
    <div
      role={live ? "alert" : undefined}
      className={cx("flex flex-col gap-3 rounded-block border border-amber/60 bg-surface-2 p-5", className)}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" size={20} className="mt-0.5 shrink-0 text-amber" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 font-semibold text-text">{title}</p>
          <p className="m-0 text-text-2">{message}</p>
          {requestId ? (
            <p className="m-0 font-mono text-label-sm break-all text-text-3">Request ID {requestId}</p>
          ) : null}
        </div>
      </div>
      {onRetry ? (
        <div>
          <Button
            variant="secondary"
            onClick={onRetry}
            disabled={retrying}
            icon={<RotateCw size={16} className={retrying ? "animate-spin" : undefined} />}
          >
            {retrying ? "Retrying…" : retryLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
