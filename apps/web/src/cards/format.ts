/** Interval labels ("10 min", "1 day", "3 days", "1.5 mo") from ui_copy, and key handling for the review screen. */

export type Translate = (key: string, vars?: Record<string, string | number>) => string;

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function oneDecimal(x: number): string {
  return x < 10 ? String(Math.round(x * 10) / 10) : String(Math.round(x));
}

/** A rating button's interval: minutes under an hour, hours under a day, then days, months, years. */
export function intervalLabel(ms: number, t: Translate): string {
  if (ms < HOUR) return t("cards.interval_minutes", { n: Math.max(1, Math.round(ms / MINUTE)) });
  if (ms < DAY) return t("cards.interval_hours", { n: Math.max(1, Math.round(ms / HOUR)) });
  const days = Math.round(ms / DAY);
  if (days === 1) return t("cards.interval_day");
  if (days < 30) return t("cards.interval_days", { n: days });
  if (days < 365) return t("cards.interval_months", { n: oneDecimal(days / 30) });
  return t("cards.interval_years", { n: oneDecimal(days / 365) });
}

/** "Tue 17 Nov, 09:00" in the student's locale and zone. */
export function whenLabel(d: Date, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export type CardKey = { type: "flip" } | { type: "rate"; rating: 1 | 2 | 3 | 4 };

export interface KeyLike {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  repeat?: boolean;
}

/** Space flips the card while the question shows; 1–4 rate while the answer shows. */
export function cardKey(e: KeyLike, flipped: boolean): CardKey | null {
  if (e.altKey || e.ctrlKey || e.metaKey || e.repeat) return null;
  if (!flipped) return e.key === " " || e.key === "Spacebar" ? { type: "flip" } : null;
  if (e.key === "1" || e.key === "2" || e.key === "3" || e.key === "4") {
    return { type: "rate", rating: Number(e.key) as 1 | 2 | 3 | 4 };
  }
  return null;
}

function scrollable(el: HTMLElement): boolean {
  const style = getComputedStyle(el);
  const y = style.overflowY;
  return (y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight;
}

/**
 * True when a key press belongs to something else: a text field, a control that handles Space itself (a focused
 * button or link), a dialog, the ink pad, media, or a scroll region the student is reading.
 */
export function ownedElsewhere(t: EventTarget | null, key = " "): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "VIDEO", "AUDIO", "IFRAME"].includes(t.tagName))
    return true;
  // A focused button or link activates on Space itself; digits still rate.
  if ((t.tagName === "BUTTON" || t.tagName === "A") && (key === " " || key === "Spacebar" || key === "Enter")) {
    return true;
  }
  if (t.closest("dialog, [role='dialog'], [data-ink-surface], video, audio, iframe, [role='slider']")) return true;
  for (let el: HTMLElement | null = t; el && el !== document.body; el = el.parentElement) {
    if (el.dataset.cardShortcuts === "on") return false;
    if (scrollable(el)) return true;
  }
  return false;
}
