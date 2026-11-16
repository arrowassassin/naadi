/** Date helpers that work in the student's own time zone (profile.timezone), not the device's. */

export function safeZone(tz: string | null | undefined): string | undefined {
  if (!tz) return undefined;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: tz });
    return tz;
  } catch {
    return undefined;
  }
}

/** YYYY-MM-DD of `now` in `tz`. */
export function localDate(now: Date, tz: string | undefined): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Whole calendar days from today (in tz) until an ISO date; negative when it has passed. */
export function daysUntil(isoDate: string, now: Date, tz: string | undefined): number {
  const today = Date.parse(`${localDate(now, tz)}T00:00:00Z`);
  const target = Date.parse(`${isoDate}T00:00:00Z`);
  return Math.round((target - today) / 86_400_000);
}

export type DayPart = "morning" | "afternoon" | "evening";

/** Part of the day in tz, for the greeting (the words themselves are ui_copy keys today.greeting_*). */
export function dayPart(now: Date, tz: string | undefined): DayPart {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "numeric", hourCycle: "h23" }).format(now),
  );
  if (hour < 5) return "evening";
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  return "evening";
}

/** "Mon 16 Nov" in tz. */
export function dayLabel(now: Date, tz: string | undefined): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" })
    .format(now)
    .replace(",", "");
}

/** "24 Dec 2026" for a calendar date (no time zone shift). */
export function formatDate(isoDate: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }).format(
    new Date(`${isoDate}T00:00:00Z`),
  );
}
