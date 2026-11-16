import { zErrorResponse } from "@naadi/shared";

/** A failed API call, normalised to the server's error envelope (`{error: {code, message, request_id}}`). */
export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(message: string, status: number | null, code: string | null, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export const UNREACHABLE = "The server could not be reached. Check your connection and try again.";
export const CONTRACT = "The server sent a response this version of the app does not understand. Reload the page.";

function isNamed(e: unknown, name: string): e is Error {
  return e instanceof Error && e.name === name;
}

export interface SdkResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/** Await a generated-SDK call and return its data, or throw an ApiError carrying the server's envelope. */
export async function unwrap<T>(call: Promise<SdkResult<T>>): Promise<T> {
  let res: SdkResult<T>;
  try {
    res = await call;
  } catch (err) {
    if (isNamed(err, "AbortError")) throw err;
    throw new ApiError(isNamed(err, "ZodError") ? CONTRACT : UNREACHABLE, null, null, null);
  }
  const status = res.response?.status ?? null;
  const headerRid = res.response?.headers.get("x-request-id") ?? null;
  if (isNamed(res.error, "AbortError")) throw res.error;
  if (isNamed(res.error, "ZodError")) throw new ApiError(CONTRACT, status, "contract", headerRid);
  if (!res.response) throw new ApiError(UNREACHABLE, null, null, null);
  if (res.data !== undefined && status !== null && status < 300) return res.data;
  const env = zErrorResponse.safeParse(res.error);
  if (env.success) {
    throw new ApiError(env.data.error.message, status, env.data.error.code, env.data.error.request_id ?? headerRid);
  }
  throw new ApiError(`The server answered with HTTP ${status}.`, status, null, headerRid);
}
