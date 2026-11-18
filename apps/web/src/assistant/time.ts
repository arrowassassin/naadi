/**
 * A server time (ISO) as the device shows it: "14:05", or "Tue 14:05" when it is not today. Invalid input
 * gives "—" (the notice still says what happened, only the time is unknown).
 */
export function formatClock(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const time = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(d);
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return time;
  const day = new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(d);
  return `${day} ${time}`;
}

/** A thread's last activity for the list: time today, otherwise the date. */
export function formatWhen(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (d.toDateString() === now.toDateString()) {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(d);
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat(
    undefined,
    sameYear ? { day: "numeric", month: "short" } : { dateStyle: "medium" },
  ).format(d);
}
