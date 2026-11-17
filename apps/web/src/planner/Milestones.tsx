import type { MilestoneOut } from "@naadi/shared";
import { Tile, TileLabel } from "@naadi/ui";
import { ClipboardCheck, Flag, Moon } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { dayMonthYear, relativeDays } from "./format";

const MAX = 7;

/** The road to exam day: what is coming up (assessments, phases, the rest day, the exam). */
export function Milestones({ items, exam }: { items: MilestoneOut[]; exam: string }) {
  const t = useUiText();
  const upcoming = items.filter((m) => m.days_away >= 0);
  // Always end on the exam; fill the rest with the soonest.
  const exams = upcoming.filter((m) => m.kind === "exam");
  const rest = upcoming.filter((m) => m.kind !== "exam").slice(0, MAX - exams.length);
  const shown = [...rest, ...exams];
  if (shown.length === 0) return null;
  const label = (m: MilestoneOut): string => {
    switch (m.kind) {
      case "assessment":
        return m.title
          ? t("planner.milestone_assessment", { title: m.title })
          : t("planner.milestone_assessment_plain");
      case "phase":
        return t("planner.milestone_phase", { phase: m.title ?? "" });
      case "rest":
        return t("planner.milestone_rest");
      case "exam":
        return t("planner.milestone_exam", { exam });
    }
  };
  const icon = (m: MilestoneOut) =>
    m.kind === "assessment" ? (
      <ClipboardCheck size={16} />
    ) : m.kind === "rest" ? (
      <Moon size={16} />
    ) : (
      <Flag size={16} />
    );
  return (
    <Tile as="section" aria-labelledby="milestones-h" className="flex flex-col gap-4">
      <TileLabel>
        <span id="milestones-h">{t("planner.milestones")}</span>
      </TileLabel>
      <ol className="m-0 flex list-none flex-col p-0">
        {shown.map((m) => (
          <li
            key={`${m.kind}-${m.date}`}
            data-milestone={m.kind}
            className="flex items-start gap-3 border-line-soft border-b py-3 first:pt-0 last:border-b-0 last:pb-0"
          >
            <span aria-hidden="true" className="mt-0.5 text-text-3">
              {icon(m)}
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="break-words text-ui text-text">{label(m)}</span>
              <span className="text-ui-sm text-text-3">
                {dayMonthYear(m.date)} · {relativeDays(t, m.days_away)}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </Tile>
  );
}
