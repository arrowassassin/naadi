import type { StageOut, TaskOut } from "@naadi/shared";
import { Button, Chip, type ChipTone, cx } from "@naadi/ui";
import { BookOpen, Check, ChevronDown, ExternalLink, MapPin } from "lucide-react";
import { useId } from "react";
import { useUiText } from "../copy/useUiText";
import { ReverifyBadge, StatusChip } from "./Badges";
import { dueRuleText, formatDate, formatMoney, hostOf, type StageState, safeHttpsUrl } from "./format";
import { Markdown } from "./Markdown";

const STATE_TONE: Record<StageState, ChipTone> = {
  done: "done",
  current: "current",
  started: "meta",
  upcoming: "planned",
};

/**
 * One stage of the route: its number and state in words, progress, the tasks with the date each was last checked and its
 * official link, the stage summary and guide. The current stage opens by default; the lime action of the page is not
 * here (it is the page's "next step").
 */
export function StageCard({
  stage,
  number,
  state,
  expanded,
  onToggle,
  tasks,
  pendingTasks,
  onOpenTask,
  onToggleDone,
  onOpenGuide,
  onSetCurrent,
  canSetCurrent,
  setCurrentReason,
}: {
  stage: StageOut;
  number: number;
  state: StageState;
  expanded: boolean;
  onToggle: () => void;
  /** The tasks to list (all of them, or only those to re-verify). */
  tasks: TaskOut[];
  pendingTasks: ReadonlySet<string>;
  onOpenTask: (taskId: string) => void;
  onToggleDone: (task: TaskOut) => void;
  onOpenGuide: (stageKey: string) => void;
  onSetCurrent: (stageKey: string) => void;
  canSetCurrent: boolean;
  setCurrentReason: string | null;
}) {
  const t = useUiText();
  const ids = useId();
  return (
    <li id={`stage-${stage.key}`} data-stage={stage.key} data-state={state} className="list-none scroll-mt-24">
      <section
        aria-labelledby={`${ids}-h`}
        className={cx(
          "grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-4 gap-y-3 rounded-tile-sm border p-5 tablet:grid-cols-[5.5rem_minmax(0,1fr)] tablet:gap-x-5 tablet:p-7",
          state === "done" && "border-line-soft bg-surface-2",
          state === "current" && "border-text bg-surface-1",
          state === "started" && "border-line-soft bg-surface-1",
          state === "upcoming" && "border-dashed border-line-dashed bg-transparent",
        )}
      >
        <span
          aria-hidden="true"
          className="font-display font-extrabold text-[2.5rem] leading-[0.85] tracking-[-0.05em] tablet:text-[4rem]"
        >
          {String(number).padStart(2, "0")}
        </span>
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h3
              id={`${ids}-h`}
              className="m-0 font-display font-bold text-[1.375rem] leading-tight tracking-[-0.02em] tablet:text-[1.625rem]"
            >
              {stage.title}
            </h3>
            <Chip
              tone={STATE_TONE[state]}
              icon={state === "done" ? <Check size={13} /> : state === "current" ? <MapPin size={13} /> : undefined}
            >
              {t(`pathway.stage_state.${state}`)}
            </Chip>
            {stage.reverify_count > 0 ? (
              <Chip tone="warning" icon={<span aria-hidden="true">!</span>}>
                {t("pathway.reverify_count", { n: String(stage.reverify_count) })}
              </Chip>
            ) : null}
          </div>
          <p className="m-0 text-text-2 text-ui-sm">
            {t("pathway.stage_progress", { done: String(stage.done), total: String(stage.total) })}
            {stage.spend_planned_usd > 0
              ? ` · ${t("pathway.stage_planned", { amount: formatMoney(stage.spend_planned_usd, "USD") })}`
              : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={`${ids}-body`}
              onClick={onToggle}
              className="inline-flex min-h-11 items-center gap-2 rounded-pill border border-line-control bg-transparent px-4 text-text text-ui-sm"
            >
              <ChevronDown
                aria-hidden="true"
                size={16}
                className={cx("transition-transform", expanded && "rotate-180")}
              />
              {t(expanded ? "pathway.hide_tasks" : "pathway.show_tasks")}
            </button>
          </div>
        </div>

        {expanded ? (
          <div id={`${ids}-body`} className="col-span-2 flex min-w-0 flex-col gap-5 border-line-soft border-t pt-5">
            {stage.summary_md ? <Markdown source={stage.summary_md} /> : null}
            <div className="flex flex-wrap gap-2">
              {stage.guide ? (
                <Button variant="secondary" icon={<BookOpen size={16} />} onClick={() => onOpenGuide(stage.key)}>
                  {t("pathway.open_guide")}
                </Button>
              ) : null}
              {state !== "current" ? (
                <Button
                  variant="secondary"
                  icon={<MapPin size={16} />}
                  disabled={!canSetCurrent}
                  onClick={() => onSetCurrent(stage.key)}
                >
                  {t("pathway.set_current")}
                </Button>
              ) : null}
              {setCurrentReason && state !== "current" ? (
                <span className="self-center text-text-3 text-ui-sm">{setCurrentReason}</span>
              ) : null}
            </div>
            {tasks.length === 0 ? (
              <p className="m-0 text-text-3">{t("pathway.no_tasks_match")}</p>
            ) : (
              <ul
                aria-label={t("pathway.tasks_of", { stage: stage.title })}
                className="m-0 flex list-none flex-col p-0"
              >
                {tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    pending={pendingTasks.has(task.id)}
                    onOpen={() => onOpenTask(task.id)}
                    onToggleDone={() => onToggleDone(task)}
                  />
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </section>
    </li>
  );
}

function TaskRow({
  task,
  pending,
  onOpen,
  onToggleDone,
}: {
  task: TaskOut;
  pending: boolean;
  onOpen: () => void;
  onToggleDone: () => void;
}) {
  const t = useUiText();
  const rule = dueRuleText(t, task.due_rule);
  const href = safeHttpsUrl(task.official_url);
  const done = task.status === "done";
  return (
    <li
      data-task={task.id}
      data-status={task.status}
      className="flex flex-col gap-2 border-line-soft border-t py-4 first:border-t-0"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button
          type="button"
          onClick={onOpen}
          className="min-h-11 min-w-0 flex-1 basis-60 bg-transparent p-0 text-left font-semibold text-text text-ui"
        >
          {task.title}
        </button>
        <button
          type="button"
          onClick={onToggleDone}
          aria-pressed={done}
          className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-pill border border-line-control bg-transparent px-4 text-text text-ui-sm"
        >
          <Check aria-hidden="true" size={15} />
          {t(done ? "pathway.undo_done" : "pathway.mark_done")}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <StatusChip status={task.status} />
        {task.reverify ? <ReverifyBadge reason={task.reverify} /> : null}
        {pending ? <span className="text-text-3 text-ui-sm">{t("pathway.will_sync")}</span> : null}
        {task.due ? (
          <span className="text-text-2 text-ui-sm">{t("pathway.due_date", { date: formatDate(task.due) })}</span>
        ) : rule ? (
          <span className="text-text-3 text-ui-sm">{rule}</span>
        ) : null}
        {task.cost_usd ? <span className="text-text-3 text-ui-sm">{formatMoney(task.cost_usd, "USD")}</span> : null}
      </div>
      <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-text-3 text-ui-sm">
        <span>
          {task.last_verified
            ? t("pathway.checked_on", { date: formatDate(task.last_verified) })
            : t("pathway.never_checked")}
        </span>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-8 items-center gap-1 text-text underline underline-offset-4"
          >
            {t(task.official ? "pathway.official_link" : "pathway.source_link", { host: hostOf(href) })}
            <ExternalLink aria-hidden="true" size={12} />
          </a>
        ) : null}
      </p>
    </li>
  );
}
