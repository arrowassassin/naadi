import type { ChapterDetail, ReadingPosition } from "@naadi/shared";
import { Chip, cx } from "@naadi/ui";
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
  return (
    <Sheet open={open} onClose={onClose} title="Contents">
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
                  <span className="text-text-3 text-ui-sm">{minutesLabel(s.reading_minutes)}</span>
                </span>
                {current ? (
                  <Chip tone="done">Reading</Chip>
                ) : p?.completed ? (
                  <Chip tone="done">Read</Chip>
                ) : p ? (
                  <Chip tone="planned">Started</Chip>
                ) : (
                  <span className="sr-only">Not started</span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </Sheet>
  );
}
