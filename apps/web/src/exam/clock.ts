/**
 * The exam timer's arithmetic. The server owns time: every timed/exam response carries `server_now` and a
 * `deadline_at`. The client keeps only an estimate of the offset between its own clock and the server's and
 * shows `deadline_at − (Date.now() + offset)`, recomputed on every tick, so the display is right after a
 * reload, a backgrounded tab or a wrong device clock. It never counts down on its own.
 */

/** One round trip to the clock endpoint, in client milliseconds (Date.now()) and the server's ISO time. */
export interface ClockSample {
  sentAt: number;
  receivedAt: number;
  serverNow: string;
}

export interface OffsetEstimate {
  /** serverTime ≈ Date.now() + offsetMs */
  offsetMs: number;
  /** The round trip of the sample the estimate comes from (its uncertainty is ±rtt/2). */
  rttMs: number;
}

/** The offset one sample implies: the server read its clock about halfway through the round trip. */
export function sampleOffset(s: ClockSample): OffsetEstimate {
  const rtt = Math.max(0, s.receivedAt - s.sentAt);
  const server = Date.parse(s.serverNow);
  return { offsetMs: server - (s.sentAt + rtt / 2), rttMs: rtt };
}

/**
 * Best-RTT estimate over the recent samples: the shortest round trip has the smallest error. Samples older
 * than `maxAgeMs` are dropped first (a sleeping device can jump its clock), keeping at least the newest one.
 */
export function bestOffset(
  samples: readonly ClockSample[],
  now: number,
  maxAgeMs = 10 * 60_000,
): OffsetEstimate | null {
  if (samples.length === 0) return null;
  const fresh = samples.filter((s) => now - s.receivedAt <= maxAgeMs);
  const pool = fresh.length ? fresh : [samples[samples.length - 1] as ClockSample];
  let best: OffsetEstimate | null = null;
  for (const s of pool) {
    const e = sampleOffset(s);
    if (!best || e.rttMs < best.rttMs) best = e;
  }
  return best;
}

/** Keep the newest `max` samples (oldest first). */
export function addSample(samples: readonly ClockSample[], s: ClockSample, max = 8): ClockSample[] {
  return [...samples, s].slice(-max);
}

/** Milliseconds left until `deadlineIso` on the server's clock (never negative). */
export function remainingMs(deadlineIso: string, offsetMs: number, now: number): number {
  return Math.max(0, Date.parse(deadlineIso) - (now + offsetMs));
}

/**
 * The timer text: the nearest whole second (with a 100 ms tick the display is never more than about 0.6 s from
 * the server's time), `m:ss`, or `h:mm:ss` above an hour (a 60-minute block starts at 60:00, as on test day).
 */
export function formatRemaining(ms: number): string {
  const total = Math.round(Math.max(0, ms) / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  if (total > 3600) return `${h}:${String(m).padStart(2, "0")}:${ss}`;
  return `${h * 60 + m}:${ss}`;
}

/** The same amount for a screen reader: whole minutes (rounded up), or seconds under a minute. */
export function spokenParts(ms: number): { minutes: number } | { seconds: number } {
  const total = Math.ceil(Math.max(0, ms) / 1000);
  return total >= 60 ? { minutes: Math.ceil(total / 60) } : { seconds: total };
}

/** Thresholds (ms left) announced once each through aria-live: 5 minutes and 1 minute. */
export const ANNOUNCE_AT = [5 * 60_000, 60_000] as const;

/**
 * The threshold crossed between two readings of the time left, if any (from above it to at or below it).
 * Reloading under a threshold does not announce it again (`prev` starts as null and only sets the baseline).
 */
export function crossedThreshold(prev: number | null, next: number): number | null {
  if (prev === null) return null;
  for (const t of ANNOUNCE_AT) if (prev > t && next <= t) return t;
  return null;
}
