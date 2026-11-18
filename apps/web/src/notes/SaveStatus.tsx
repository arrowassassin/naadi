import { cx } from "@naadi/ui";
import { Check, CloudOff, LoaderCircle, TriangleAlert } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { refusedKey, usePendingOps, useUserId } from "./api";
import { dropOp } from "./outbox";

/**
 * A note's save state as icon + word (never colour alone): Saved · Saving… · Saved on this device, will sync ·
 * Couldn't save (with the reason and Discard, which drops the change the server refused).
 */
export function NoteSaveStatus({
  ids,
  className,
  tone = "dark",
  dirty = false,
}: {
  ids: readonly string[];
  className?: string;
  tone?: "dark" | "paper";
  /** Edited on screen and not yet queued (the save waits for a pause in typing). */
  dirty?: boolean;
}) {
  const t = useUiText();
  const online = useOnlineStatus();
  const userId = useUserId();
  const ops = usePendingOps().filter((o) => ids.includes(o.id));
  const refused = ops.find((o) => o.refused);
  const pending = dirty || ops.some((o) => !o.refused);
  const muted = tone === "paper" ? "text-paper-text-2" : "text-text-3";
  if (refused?.refused) {
    return (
      <span
        role="alert"
        data-note-save="error"
        className={cx("flex flex-wrap items-center gap-2 text-ui-sm", className)}
      >
        <TriangleAlert aria-hidden="true" size={16} className="shrink-0" />
        <span>{t(refusedKey(refused.refused))}</span>
        <button
          type="button"
          onClick={() => userId && void dropOp(userId, refused.id)}
          className={cx(
            "min-h-11 rounded-pill border border-current bg-transparent px-3 text-ui-sm",
            tone === "paper" ? "text-ground" : "text-text",
          )}
        >
          {t("notes.save_discard")}
        </button>
      </span>
    );
  }
  const state = pending ? (online ? "saving" : "local") : "saved";
  const Icon = state === "saving" ? LoaderCircle : state === "local" ? CloudOff : Check;
  return (
    <span role="status" data-note-save={state} className={cx("flex items-center gap-1.5 text-ui-sm", muted, className)}>
      <Icon aria-hidden="true" size={15} className={cx("shrink-0", state === "saving" && "motion-safe:animate-spin")} />
      {t(`notes.save_${state}`)}
    </span>
  );
}
