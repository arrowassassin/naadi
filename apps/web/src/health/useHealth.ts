import { getHealth, type HealthResponse, zErrorResponse, zHealthResponse } from "@naadi/shared";
import { useQuery } from "@tanstack/react-query";

export interface HealthResult {
  health: HealthResponse;
  httpStatus: number;
  requestId: string | null;
}

export class HealthRequestError extends Error {
  readonly status: number | null;
  readonly requestId: string | null;

  constructor(message: string, status: number | null, requestId: string | null) {
    super(message);
    this.name = "HealthRequestError";
    this.status = status;
    this.requestId = requestId;
  }
}

const UNREACHABLE = "The API could not be reached. Check your connection and try again.";
const CONTRACT = "The API sent a response this version of the app does not understand.";

function isNamedError(e: unknown, name: string): boolean {
  return e instanceof Error && e.name === name;
}

/** GET /api/health through the generated client (zod-validated). A 503 still carries a valid report. */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResult> {
  let res: Awaited<ReturnType<typeof getHealth>>;
  try {
    res = await getHealth({ signal });
  } catch (err) {
    if (isNamedError(err, "AbortError")) throw err;
    throw new HealthRequestError(isNamedError(err, "ZodError") ? CONTRACT : UNREACHABLE, null, null);
  }
  const status = res.response?.status ?? null;
  const requestId = res.response?.headers.get("x-request-id") ?? null;
  if (isNamedError(res.error, "AbortError")) throw res.error;
  // The generated client reports a body that fails its zod schema as a ZodError, whatever the status.
  if (isNamedError(res.error, "ZodError")) throw new HealthRequestError(CONTRACT, status, requestId);
  if (!res.response) throw new HealthRequestError(UNREACHABLE, null, null);
  if (res.data !== undefined && status !== null && status < 300) {
    return { health: res.data, httpStatus: status, requestId };
  }
  if (status === 503) {
    const report = zHealthResponse.safeParse(res.error);
    if (report.success) return { health: report.data, httpStatus: 503, requestId };
  }
  const envelope = zErrorResponse.safeParse(res.error);
  if (envelope.success) {
    throw new HealthRequestError(
      `${envelope.data.error.message} (HTTP ${status})`,
      status,
      envelope.data.error.request_id ?? requestId,
    );
  }
  throw new HealthRequestError(`The API answered with HTTP ${status}.`, status, requestId);
}

export const HEALTH_QUERY_KEY = ["system", "health"] as const;

export function useHealth() {
  return useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: 60_000,
    networkMode: "online",
  });
}
