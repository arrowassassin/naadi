import type { SdkResult } from "../auth/apiError";
import { observeServerClock, serverNow } from "../ink/persist/clock";

/**
 * Edit times for notes, notebook pages and notebooks (segment 13 rework: bounded last-writer-wins, the ink rule of
 * segment 12b). Every queued write is stamped when the student MAKES the edit, on the server's clock as this device
 * sees it (`serverNow()` of the ink clock, one offset per page), and sent as `client_updated_at`. The server keeps
 * the newer edit: a write older than the stored one is refused (409 *_conflict) with the stored copy.
 *
 * A stamp is never older than the version this device last saw of that note (`seen`): an edit made after reading a
 * version is at least as new as it. That keeps a device whose clock offset was corrected between two of its own
 * edits (its first answer arrived) from losing against its own earlier write; ties pass on the server.
 *
 * Keys: a note's or page's id; a notebook's own fields use `nb:<id>`.
 */
const seen = new Map<string, number>();

function toMs(t: string | number | null | undefined): number | null {
  if (typeof t === "number") return Number.isFinite(t) ? t : null;
  if (typeof t !== "string") return null;
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

/** Remember the edit time of a server copy this device has seen. */
export function noteSeen(key: string, editedAt: string | number | null | undefined): void {
  const ms = toMs(editedAt);
  if (ms === null) return;
  if (ms > (seen.get(key) ?? 0)) seen.set(key, ms);
}

/** The edit time of an edit made now: the server clock, never before what was seen of it, nor before `prior`. */
export function editStamp(key: string, prior?: number | null): number {
  return Math.max(serverNow(), seen.get(key) ?? 0, prior ?? 0);
}

export const notebookClockKey = (id: string) => `nb:${id}`;

/** Test hook. */
export function resetEditClockForTests(): void {
  seen.clear();
}

/**
 * Feed the ink clock from a Notes API answer. Its `Date` header has whole seconds, so the sample is taken at the
 * middle of that second (half a second of error, well inside the clock's 2 s dead band and the server's 5 min).
 * Only answers to writes are used: a GET may come from an HTTP cache with an old date.
 */
export function observeDateHeader(response: Response | undefined, sentAt: number): void {
  const raw = response?.headers.get("date");
  const at = raw ? Date.parse(raw) : Number.NaN;
  if (Number.isFinite(at)) observeServerClock(at + 500, sentAt, Date.now());
}

/**
 * Wrap a generated-SDK call so its raw result stays readable after `authed()` threw: a 409 conflict's body (the
 * stored copy) is not part of the error envelope `ApiError` keeps. Also feeds the server clock from the answer.
 */
export function captured<T>(make: (headers: Record<string, string>) => Promise<SdkResult<T>>) {
  let last: SdkResult<T> | undefined;
  let sentAt = 0;
  return {
    make: (headers: Record<string, string>) => {
      sentAt = Date.now();
      return make(headers).then((r) => {
        last = r;
        observeDateHeader(r.response, sentAt);
        return r;
      });
    },
    /** The error body of the last answer (parsed JSON), if any. */
    errorBody: (): unknown => last?.error,
    sentAt: () => sentAt,
  };
}

export { observeServerClock };
