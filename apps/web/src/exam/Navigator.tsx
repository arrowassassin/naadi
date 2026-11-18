import type { BlockQuestionState } from "@naadi/shared";
import { cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { Flag } from "lucide-react";
import { useCopy } from "../practice/copy";

export type TileStatus = "current" | "answered" | "unanswered";

/** The tile's status and accessible name parts (status is never colour alone: the name says it, a glyph shows it). */
export function tileOf(
  q: BlockQuestionState,
  currentN: number,
  localChoice?: boolean,
): {
  status: TileStatus;
  flagged: boolean;
} {
  const answered = q.answered || localChoice === true;
  return { status: q.n === currentN ? "current" : answered ? "answered" : "unanswered", flagged: q.flagged };
}

const TILE =
  "relative flex h-11 min-w-11 items-center justify-center rounded-[12px] font-mono text-[0.8125rem] no-underline box-border";

/**
 * The section's question navigator (Exam board): answered = filled, unanswered = dashed, flagged = amber ring
 * plus a flag glyph and the word in its name, current = lime. Numbers are the question's position in the section.
 */
export function Navigator({
  blockId,
  questions,
  firstN,
  currentN,
  chosenLocally,
  onPick,
  className,
}: {
  blockId: string;
  questions: BlockQuestionState[];
  firstN: number;
  currentN: number;
  /** Questions answered on this device but not yet confirmed by the server (offline). */
  chosenLocally: ReadonlySet<number>;
  onPick?: () => void;
  className?: string;
}) {
  const t = useCopy();
  return (
    <nav aria-label={t("exam.navigator")} className={className}>
      <ol className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(44px,1fr))] gap-2 p-0">
        {questions.map((q) => {
          const pos = q.n - firstN + 1;
          const { status, flagged } = tileOf(q, currentN, chosenLocally.has(q.n));
          const words = [
            t("exam.tile.question", { n: pos }),
            t(
              status === "current"
                ? "exam.tile.current"
                : status === "answered"
                  ? "exam.tile.answered"
                  : "exam.tile.unanswered",
            ),
            flagged ? t("exam.tile.flagged") : null,
          ].filter(Boolean);
          return (
            <li key={q.n} className="flex">
              <Link
                to="/practice/blocks/$blockId/$n"
                params={{ blockId, n: q.n }}
                replace
                onClick={onPick}
                aria-label={words.join(", ")}
                className={cx(
                  TILE,
                  "w-full",
                  status === "current" && "border-0 bg-lime font-bold text-lime-ink hover:text-lime-ink",
                  status === "answered" && "border-0 bg-line text-text hover:text-text",
                  status === "unanswered" && "border border-line-dashed border-dashed bg-transparent text-text-2",
                  flagged && status !== "current" && "border-2 border-amber border-solid text-amber hover:text-amber",
                  flagged && status === "current" && "outline-2 outline-amber outline-offset-2",
                )}
              >
                {pos}
                {flagged ? (
                  <Flag
                    aria-hidden="true"
                    size={10}
                    className={cx("absolute top-1 right-1", status === "current" ? "text-lime-ink" : "text-amber")}
                    fill="currentColor"
                  />
                ) : null}
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
