import type { WeekItemOut } from "@naadi/shared";
import type { T } from "../today/format";

/** A calendar date (YYYY-MM-DD) as a local Date at midnight: no time-zone shift. */
export function dateOf(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y as number, (m as number) - 1, d as number);
}

const fmt = (opts: Intl.DateTimeFormatOptions) => (iso: string) =>
  new Intl.DateTimeFormat("en-GB", opts).format(dateOf(iso));

export const dayNumber = fmt({ day: "numeric" });
export const dayMonth = fmt({ day: "numeric", month: "short" });
export const dayMonthYear = fmt({ day: "numeric", month: "short", year: "numeric" });
export const weekdayShort = fmt({ weekday: "short" });
export const weekdayLong = fmt({ weekday: "long", day: "numeric", month: "long" });

export function range(t: T, start: string, end: string): string {
  const sameYear = dateOf(start).getFullYear() === dateOf(end).getFullYear();
  return t("planner.range", { from: sameYear ? dayMonth(start) : dayMonthYear(start), to: dayMonthYear(end) });
}

/** A count with its own singular wording: `key` for n ≠ 1 (`{n}`), `key_one` for 1. */
export function counted(t: T, key: string, n: number): string {
  return n === 1 ? t(`${key}_one`) : t(key, { n: String(n) });
}

export function hours(h: number): string {
  return String(Math.round(h * 10) / 10);
}

export function relativeDays(t: T, daysAway: number): string {
  if (daysAway === 0) return t("planner.milestone_today");
  if (daysAway === 1) return t("planner.in_one_day");
  if (daysAway === -1) return t("planner.one_day_ago");
  return daysAway > 0 ? t("planner.in_days", { n: String(daysAway) }) : t("planner.days_ago", { n: String(-daysAway) });
}

export const isOpen = (i: Pick<WeekItemOut, "status">): boolean =>
  i.status === "planned" || i.status === "now" || i.status === "behind";
