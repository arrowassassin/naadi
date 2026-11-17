/**
 * The server's clock as this device sees it (segment 12b). Ink is merged last-writer-wins on `updatedAt`. If a
 * device's clock runs minutes ahead, every stamp it writes is in the future, so its versions would beat later
 * edits and deletions from other devices. The server clamps such stamps and echoes the clamped copy, but it is
 * better not to write them at all. Every ink response carries `server_now`; we keep the offset to it and stamp
 * with `serverNow()`.
 *
 * - One offset per page, shared by every surface.
 * - A sample is the server time minus the request's local midpoint, accepted only when the round trip was short
 *   (under 10 s). The offset only moves when a sample differs from it by more than 2 s. Network jitter would
 *   otherwise make stamps step backwards between two edits of the same stroke, and a few seconds of skew is far
 *   inside the server's 5-minute window.
 */
const RTT_LIMIT_MS = 10_000;
const DEADBAND_MS = 2_000;

let offset = 0;

export function serverNow(): number {
  return Date.now() + offset;
}

/** Feed one response: `server` is its `server_now`, `sentAt` and `receivedAt` are local Date.now() readings. */
export function observeServerClock(server: number, sentAt: number, receivedAt: number): void {
  if (!Number.isFinite(server) || server <= 0) return;
  const rtt = receivedAt - sentAt;
  if (rtt < 0 || rtt > RTT_LIMIT_MS) return;
  const sample = Math.round(server - (sentAt + receivedAt) / 2);
  if (Math.abs(sample - offset) > DEADBAND_MS) offset = sample;
}

export function serverClockOffset(): number {
  return offset;
}

/** Test hook. */
export function resetServerClockForTests(): void {
  offset = 0;
}
