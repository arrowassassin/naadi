import type { ChapterDetail, ReadingPosition } from "@naadi/shared";
import { Chip, cx } from "@naadi/ui";
import { useUiText } from "../copy/useUiText";
import { minutesLabel } from "./format";
import { Sheet } from "./Sheet";

export function ContentsSheet({
  open,
  onClose,
  detail,
  currentId,
  positions,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  detail: ChapterDetail;
  currentId: string;
  positions: Map<string, ReadingPosition>;
  onPick: (sectionId: string) => void;
}) {
  const t = useUiText();
  return (
    <Sheet open={open} onClose={onClose} title={t("learn.contents")} closeLabel={t("learn.close")}>
      <p className="mt-0 mb-3 text-text-2 text-ui-sm">{detail.title}</p>
      <ol className="m-0 flex list-none flex-col p-0">
        {detail.sections.map((s, i) => {
          const p = positions.get(s.id);
          const current = s.id === currentId;
          return (
            <li key={s.id} className="border-line-soft border-t first:border-t-0">
              <button
                type="button"
                onClick={() => onPick(s.id)}
                aria-current={current ? "page" : undefined}
                className={cx(
                  "grid min-h-14 w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 px-1 py-2 text-left",
                  current ? "text-text" : "text-text-2 hover:text-text",
                )}
              >
                <span className="font-mono text-label-sm text-text-3">{String(i + 1).padStart(2, "0")}</span>
                <span className="flex min-w-0 flex-col">
                  <span className={cx("truncate", current && "font-semibold")}>{s.heading}</span>
                  <span className="text-text-3 text-ui-sm">{minutesLabel(t, s.reading_minutes)}</span>
                </span>
                {current ? (
                  <Chip tone="done">{t("learn.contents_reading")}</Chip>
                ) : p?.completed ? (
                  <Chip tone="done">{t("learn.contents_read")}</Chip>
                ) : p ? (
                  <Chip tone="planned">{t("learn.contents_started")}</Chip>
                ) : (
                  <span className="sr-only">{t("learn.status_not_started")}</span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </Sheet>
  );
}
