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

export function greeting(now: Date, tz: string | undefined): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "numeric", hourCycle: "h23" }).format(now),
  );
  if (hour < 5) return "Good evening";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
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

export const EXAM_LABEL: Record<string, string> = {
  step1: "Step 1",
  step2ck: "Step 2 CK",
  step3: "Step 3",
};
