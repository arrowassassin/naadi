import { cx } from "@naadi/ui";
import { CloudCheck, CloudOff, LoaderCircle, TriangleAlert } from "lucide-react";
import { useUiText } from "../../copy/useUiText";
import type { InkLayerHandle } from "./useInkLayer";

/**
 * The save state of a surface's ink, always as an icon plus a word (never colour alone): "Saved", "Saving…",
 * "Saved on this device · will sync", "Couldn't save · Retry". Nothing while the device copy is being read.
 */
export function InkSaveStatus({ layer, className }: { layer: InkLayerHandle; className?: string }) {
  const t = useUiText();
  const base = cx("inline-flex min-h-11 items-center gap-1.5 text-text-2 text-ui-sm", className);
  switch (layer.state) {
    case "loading":
      return null;
    case "saved":
      return (
        <span role="status" data-ink-save="saved" className={base}>
          <CloudCheck aria-hidden="true" size={16} />
          {t("ink.save.saved")}
        </span>
      );
    case "saving":
      return (
        <span role="status" data-ink-save="saving" className={base}>
          <LoaderCircle aria-hidden="true" size={16} className="motion-safe:animate-spin" />
          {t("ink.save.saving")}
        </span>
      );
    case "local":
      return (
        <span role="status" data-ink-save="local" className={base}>
          <CloudOff aria-hidden="true" size={16} />
          {t("ink.save.local")}
        </span>
      );
    case "error":
      return (
        <span role="alert" data-ink-save="error" className={cx(base, "text-amber")}>
          <TriangleAlert aria-hidden="true" size={16} />
          {layer.failure === "limit" ? t("ink.save.full") : t("ink.save.error")}
          <span aria-hidden="true">·</span>
          <button
            type="button"
            onClick={layer.retry}
            className="min-h-11 rounded-pill px-2 font-semibold text-amber underline underline-offset-2 hover:text-text"
          >
            {t("ink.save.retry")}
          </button>
        </span>
      );
  }
}
