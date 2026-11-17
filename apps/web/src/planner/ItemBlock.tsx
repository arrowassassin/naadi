import type { WeekItemOut } from "@naadi/shared";
import { Chip, cx } from "@naadi/ui";
import { AlertTriangle, Check, ClipboardCheck, Clock, CornerDownRight, ExternalLink } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { duration, itemTitle } from "../today/format";
import { dayMonth } from "./format";

/** The status words an item can carry on screen. Planned is the default and says nothing. */
export function ItemChips({ item, pending }: { item: WeekItemOut; pending: boolean }) {
  const t = useUiText();
  return (
    <>
      {item.kind === "assessment" ? (
        <Chip tone="current" icon={<ClipboardCheck size={12} />}>
          {t("planner.assessment")}
        </Chip>
      ) : null}
      {item.status === "done" ? (
        <Chip tone="done" icon={<Check size={12} />}>
          {t("planner.status_done")}
        </Chip>
      ) : null}
      {item.status === "skipped" ? <Chip tone="planned">{t("planner.status_skipped")}</Chip> : null}
      {item.behind ? (
        <Chip tone="warning" icon={<AlertTriangle size={12} />}>
          {t("planner.behind")}
        </Chip>
      ) : null}
      {item.moved_from ? (
        <Chip tone="meta" icon={<CornerDownRight size={11} />} className="max-w-full whitespace-normal text-left">
          {t("planner.moved_from", { date: dayMonth(item.moved_from) })}
        </Chip>
      ) : null}
      {item.ref_type === "external" ? (
        <Chip tone="meta" icon={<ExternalLink size={11} />} className="max-w-full whitespace-normal text-left">
          {t("planner.sheet_outside")}
        </Chip>
      ) : null}
      {pending ? (
        <Chip tone="meta" icon={<Clock size={11} />}>
          {t("planner.will_sync")}
        </Chip>
      ) : null}
    </>
  );
}

/** One plan item as a block you can open (a button): its kind in words, title, size and status words. */
export function ItemBlock({
  item,
  pending,
  onOpen,
  compact = false,
}: {
  item: WeekItemOut;
  pending: boolean;
  onOpen: (item: WeekItemOut) => void;
  compact?: boolean;
}) {
  const t = useUiText();
  const done = item.status === "done";
  const skipped = item.status === "skipped";
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      onClick={() => onOpen(item)}
      data-item-kind={item.kind}
      data-item-status={item.status}
      data-behind={item.behind || undefined}
      className={cx(
        "flex w-full min-w-0 flex-col items-start gap-1.5 rounded-block-sm border p-3 text-left text-text hover:border-text-3",
        item.kind === "assessment" ? "border-text bg-surface-1" : "border-line-soft bg-surface-2",
        item.behind && "border-amber",
        skipped && "border-dashed border-line-dashed bg-transparent",
        item.origin === "user" && "border-l-[4px] border-l-paper",
        compact ? "min-h-14" : "min-h-16",
      )}
    >
      <span className="font-mono text-label-sm text-text-3 uppercase">{t(`today.kind_${item.kind}`)}</span>
      <span
        className={cx(
          "break-words text-ui leading-snug",
          compact && "line-clamp-3",
          (done || skipped) && "text-text-3 line-through",
        )}
      >
        {itemTitle(t, item)}
      </span>
      {item.target_minutes || (item.target_count && item.kind === "questions") ? (
        <span className="text-ui-sm text-text-3">
          {[
            item.target_minutes ? duration(t, item.target_minutes) : null,
            item.target_count && item.kind === "questions"
              ? t("today.item_count", { n: String(item.target_count) })
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      ) : null}
      <span className="flex min-w-0 max-w-full flex-wrap gap-1.5 empty:hidden">
        <ItemChips item={item} pending={pending} />
      </span>
    </button>
  );
}
