import { Button } from "@naadi/ui";
import { AlertTriangle, PauseCircle } from "lucide-react";
import { AiLabel, useAltText } from "./AiLabel";
import type { GeneratedItem } from "./api";
import { isWorking } from "./api";
import { useGenerationCopy } from "./copy";
import { GenerationProgress } from "./Progress";

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

function formatTime(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? timeFmt.format(d) : "";
}

export interface GeneratedMediaProps {
  item: GeneratedItem;
  /** Shown on a failed item. Absent: no retry button (e.g. in the Library). */
  onRetry?: () => void;
  /** Absent: no "open in the side panel" button (nothing to open into). */
  onOpen?: () => void;
  /** Smaller frame for grids. */
  compact?: boolean;
}

/**
 * One generated image or clip in every state: progress while it is made, the labelled media when ready, a paused
 * state with the reset time, and a failure with a retry. Violet frame (assistant output); the label chip, the alt
 * text and the caption all carry "AI-generated".
 */
export function GeneratedMedia({ item, onRetry, onOpen, compact }: GeneratedMediaProps) {
  const t = useGenerationCopy();
  const alt = useAltText();
  const frame = "flex flex-col gap-3 rounded-block border border-violet-line bg-violet-ground p-4";
  const label = <AiLabel className="self-start" />;

  if (isWorking(item)) {
    return (
      <figure className={`${frame} m-0`} data-testid="generated-media" data-status={item.status}>
        {label}
        <GenerationProgress stage={item.status === "queued" ? "queued" : "generating"} />
      </figure>
    );
  }
  if (item.status === "paused") {
    return (
      <figure className={`${frame} m-0`} data-testid="generated-media" data-status="paused">
        {label}
        <div role="status" className="flex items-start gap-2 text-text">
          <PauseCircle aria-hidden="true" size={20} className="mt-0.5 shrink-0 text-violet-text" />
          <div className="flex flex-col gap-1">
            <p className="m-0 font-semibold">{t("generation.paused_title")}</p>
            <p className="m-0 text-ui-sm text-text-2">
              {t("generation.paused_body", { time: formatTime(item.paused_until) })}
            </p>
          </div>
        </div>
      </figure>
    );
  }
  if (item.status === "failed" || !item.url) {
    return (
      <figure className={`${frame} m-0`} data-testid="generated-media" data-status="failed">
        {label}
        <div role="alert" className="flex items-start gap-2 text-text">
          <AlertTriangle aria-hidden="true" size={20} className="mt-0.5 shrink-0 text-amber" />
          <div className="flex flex-col gap-1">
            <p className="m-0 font-semibold">{t("generation.failed_title")}</p>
            <p className="m-0 text-ui-sm text-text-2">
              {t(item.error ? `generation.error_${item.error}` : "generation.error_generic")}
            </p>
          </div>
        </div>
        {onRetry ? (
          <Button variant="secondary" onClick={onRetry} className="self-start">
            {t("generation.retry")}
          </Button>
        ) : null}
      </figure>
    );
  }
  const frameSize = compact ? "max-h-56" : "max-h-[70dvh]";
  return (
    <figure className={`${frame} m-0`} data-testid="generated-media" data-status="ready" data-kind={item.kind}>
      {label}
      {item.kind === "image" ? (
        <img
          src={item.url}
          alt={alt(item.prompt)}
          width={item.width ?? undefined}
          height={item.height ?? undefined}
          loading="lazy"
          className={`h-auto w-full rounded-tile-sm bg-surface-1 object-contain ${frameSize}`}
        />
      ) : (
        <video
          src={item.url}
          aria-label={alt(item.prompt)}
          controls
          muted
          loop
          playsInline
          preload="metadata"
          width={item.width ?? undefined}
          height={item.height ?? undefined}
          className={`h-auto w-full rounded-tile-sm bg-surface-1 ${frameSize}`}
        />
      )}
      <figcaption className="text-ui-sm text-text-2 break-words">
        {t("generation.caption", { prompt: item.prompt })}
      </figcaption>
      {onOpen ? (
        <Button variant="secondary" onClick={onOpen} className="self-start">
          {t("generation.open_canvas")}
        </Button>
      ) : null}
    </figure>
  );
}
