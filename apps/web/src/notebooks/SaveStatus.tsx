import { cx } from "@naadi/ui";
import { CloudCheck, CloudOff, LoaderCircle, TriangleAlert } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import type { NotebookSync } from "./api";
import { pendingFor } from "./overlay";

/**
 * Notebook edits' save state, always icon + word: "Saved", "Saving…", "Saved on this device · will sync"
 * (with the count), or the reason the server refused the last edit with "Retry".
 */
export function NotebookSaveStatus({
  sync,
  notebookId,
  className,
  quietWhenSaved = false,
}: {
  sync: NotebookSync;
  notebookId: string | null;
  className?: string;
  /** Show nothing when everything is saved (a view that already shows another save state, e.g. the ink's). */
  quietWhenSaved?: boolean;
}) {
  const t = useUiText();
  const base = cx("inline-flex min-h-11 items-center gap-1.5 text-text-2 text-ui-sm", className);
  const pending = pendingFor(notebookId, sync.queue);
  if (!sync.ready) return null;
  if (sync.state === "error" && sync.lastError) {
    return (
      <span role="alert" data-notebook-save="error" className={cx(base, "text-amber")}>
        <TriangleAlert aria-hidden="true" size={16} />
        {t(sync.lastError)}
        <span aria-hidden="true">·</span>
        <button
          type="button"
          onClick={() => sync.dismissError()}
          className="min-h-11 rounded-pill px-2 font-semibold text-amber underline underline-offset-2 hover:text-text"
        >
          {t("notebooks.save.dismiss")}
        </button>
      </span>
    );
  }
  if (pending > 0 && sync.state === "saving")
    return (
      <span role="status" data-notebook-save="saving" className={base}>
        <LoaderCircle aria-hidden="true" size={16} className="motion-safe:animate-spin" />
        {t("notebooks.save.saving")}
      </span>
    );
  if (pending > 0)
    return (
      <span role="status" data-notebook-save="local" className={base}>
        <CloudOff aria-hidden="true" size={16} />
        {pending === 1 ? t("notebooks.save.local_one") : t("notebooks.save.local", { count: String(pending) })}
      </span>
    );
  if (quietWhenSaved) return <span data-notebook-save="saved" hidden />;
  return (
    <span role="status" data-notebook-save="saved" className={base}>
      <CloudCheck aria-hidden="true" size={16} />
      {t("notebooks.save.saved")}
    </span>
  );
}
