import type { WeekDayOut, WeekItemOut, WeekOut } from "@naadi/shared";
import { Chip, cx } from "@naadi/ui";
import { AlertTriangle, ClipboardCheck } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { dayNumber, hours, weekdayLong, weekdayShort } from "./format";
import { ItemBlock } from "./ItemBlock";

interface Common {
  week: WeekOut;
  pending: ReadonlySet<string>;
  onOpen: (item: WeekItemOut) => void;
}

function DayFlags({ day }: { day: WeekDayOut }) {
  const t = useUiText();
  const behind = day.items.some((i) => i.behind);
  return (
    <>
      {day.assessment ? (
        <Chip tone="current" icon={<ClipboardCheck size={12} />}>
          {t("planner.assessment")}
        </Chip>
      ) : null}
      {behind ? (
        <Chip tone="warning" icon={<AlertTriangle size={12} />}>
          {t("planner.behind")}
        </Chip>
      ) : null}
      {day.rest ? <Chip tone="meta">{t("planner.day_rest")}</Chip> : null}
    </>
  );
}

function DayHours({ day }: { day: WeekDayOut }) {
  const t = useUiText();
  if (!day.in_plan) {
    return <span className="text-ui-sm text-text-3">{t(day.day_off ? "planner.day_off" : "planner.day_outside")}</span>;
  }
  return (
    <span className="text-ui-sm text-text-3">
      {day.planned_hours > 0 || (day.target_hours ?? 0) > 0
        ? t("planner.day_hours", { h: hours(day.planned_hours) })
        : t("planner.day_empty")}
    </span>
  );
}

/** Laptop and iPad landscape: seven columns of study blocks. */
export function WeekGrid({ week, pending, onOpen }: Common) {
  const t = useUiText();
  return (
    <ol
      aria-label={t("planner.calendar")}
      data-testid="week-grid"
      className="m-0 grid list-none grid-cols-7 overflow-hidden rounded-tile border border-line-soft bg-surface-1 p-0"
    >
      {week.days.map((d, n) => (
        <li
          key={d.date}
          data-day={d.date}
          data-today={d.is_today || undefined}
          data-assessment={d.assessment || undefined}
          className={cx(
            "flex min-h-[26rem] min-w-0 flex-col gap-2.5 p-3.5",
            n > 0 && "border-line-soft border-l",
            d.is_today && "bg-surface-2",
          )}
        >
          <div className="flex flex-col gap-1">
            <span className="font-mono text-label-sm text-text-3 uppercase">{weekdayShort(d.date)}</span>
            <span className="font-display font-extrabold text-[2rem] leading-none tracking-[-0.04em]">
              {dayNumber(d.date)}
            </span>
            <DayHours day={d} />
          </div>
          <div className="flex flex-wrap gap-1.5 empty:hidden">
            {d.is_today ? <Chip tone="current">{t("planner.day_today")}</Chip> : null}
            <DayFlags day={d} />
          </div>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {d.items.map((i) => (
              <li key={i.id}>
                <ItemBlock item={i} pending={pending.has(i.id)} onOpen={onOpen} compact />
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

/** iPad portrait and phone: the week as a strip of seven days, and the chosen day's list below it. */
export function WeekStrip({
  week,
  selected,
  onSelect,
}: {
  week: WeekOut;
  selected: string;
  onSelect: (date: string) => void;
}) {
  const t = useUiText();
  return (
    <ul aria-label={t("planner.week_strip")} className="m-0 grid list-none grid-cols-7 gap-1.5 p-0 tablet:gap-2">
      {week.days.map((d) => {
        const on = d.date === selected;
        const behind = d.items.some((i) => i.behind);
        return (
          <li key={d.date}>
            <button
              type="button"
              aria-pressed={on}
              data-day={d.date}
              data-today={d.is_today || undefined}
              onClick={() => onSelect(d.date)}
              className={cx(
                "flex min-h-16 w-full flex-col items-center justify-center gap-0.5 rounded-block-sm border px-1 py-2",
                on ? "border-text bg-text text-ground" : "border-line text-text hover:border-text-3",
                d.is_today && !on && "border-text-3",
              )}
            >
              <span className="font-mono text-label-sm uppercase">{weekdayShort(d.date).slice(0, 3)}</span>
              <span className="font-display font-bold text-[1.25rem] leading-none">{dayNumber(d.date)}</span>
              <span className="flex h-4 items-center gap-0.5">
                {d.assessment ? (
                  <>
                    <ClipboardCheck aria-hidden="true" size={12} />
                    <span className="sr-only">{t("planner.assessment")}</span>
                  </>
                ) : null}
                {behind ? (
                  <>
                    <AlertTriangle aria-hidden="true" size={12} />
                    <span className="sr-only">{t("planner.behind")}</span>
                  </>
                ) : null}
                {d.is_today ? <span className="sr-only">{t("planner.day_today")}</span> : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function DayList({
  day,
  pending,
  onOpen,
}: {
  day: WeekDayOut;
  pending: ReadonlySet<string>;
  onOpen: Common["onOpen"];
}) {
  const t = useUiText();
  return (
    <section
      aria-label={t("planner.day_list", { day: weekdayLong(day.date) })}
      data-testid="day-list"
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 font-display font-bold text-[1.375rem] leading-tight">{weekdayLong(day.date)}</h2>
        {day.is_today ? <Chip tone="current">{t("planner.day_today")}</Chip> : null}
        <DayFlags day={day} />
        <DayHours day={day} />
      </div>
      {day.items.length === 0 ? (
        <p className="m-0 text-ui text-text-3">
          {t(day.in_plan ? "planner.day_empty" : day.day_off ? "planner.day_off" : "planner.day_outside")}
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {day.items.map((i) => (
            <li key={i.id}>
              <ItemBlock item={i} pending={pending.has(i.id)} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
