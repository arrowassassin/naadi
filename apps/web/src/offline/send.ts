import { authed } from "../auth/api";
import { ApiError, type SdkResult } from "../auth/apiError";
import type { SendOutcome } from "./outbox";

/**
 * One outbox request: the generated SDK call with the op's `Idempotency-Key`, its answer mapped to an outcome.
 *
 * - No answer (offline, DNS, CORS, aborted): `unreachable`, the lane stops and waits for the connection.
 * - 401 after the refresh `authed` already tried, 408, 425, 429, 5xx: `retry` with backoff.
 * - Any other 4xx: `failed` (parked with its code, Retry / Discard in the sync sheet). 409 is the entity's to
 *   interpret first (`onError`), since for some endpoints it is the answer (a conflict with the stored row).
 */
export async function sendOp<T>(
  opId: string,
  make: (headers: Record<string, string>) => Promise<SdkResult<T>>,
  ok: (data: T) => SendOutcome,
  onError?: (err: ApiError) => SendOutcome | null,
): Promise<SendOutcome> {
  try {
    const data = await authed<T>((h) => make({ ...headersOf(h), "idempotency-key": opId }));
    return ok(data);
  } catch (err) {
    if (err instanceof ApiError) {
      const own = onError?.(err);
      if (own) return own;
    }
    return classify(err);
  }
}

function headersOf(h: Headers | Record<string, string>): Record<string, string> {
  if (typeof Headers !== "undefined" && h instanceof Headers) return Object.fromEntries(h.entries());
  return { ...(h as Record<string, string>) };
}

export function classify(err: unknown): SendOutcome {
  if (!(err instanceof ApiError) || err.status === null) return { kind: "unreachable" };
  const s = err.status;
  // The first request with this Idempotency-Key is still running on the server: ask again shortly.
  if (s === 409 && err.code === "idempotency_in_progress") return { kind: "retry", code: err.code };
  if (s === 401 || s === 408 || s === 425 || s === 429 || s >= 500)
    return { kind: "retry", code: err.code ?? `http_${s}` };
  return { kind: "failed", code: err.code ?? `http_${s}` };
}
