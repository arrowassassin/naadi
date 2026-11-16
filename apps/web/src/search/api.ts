import { type SearchResponse, searchLocal, searchWeb, type WebResponse } from "@naadi/shared";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";

export type Scope = "all" | "chapters" | "questions" | "notes" | "handwriting" | "uploads" | "pathway" | "web";

/** Results are only as fresh as the index; a repeated query within a minute is served from memory. */
const STALE_MS = 60_000;

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** Retry once on a network blip; never on an answer from the server (rate limit, unavailable). */
function retry(count: number, err: unknown): boolean {
  return count < 1 && err instanceof ApiError && err.status === null;
}

export function useLocalSearch(q: string, scope: Exclude<Scope, "web">, enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["search", "local", userId, scope, q],
    enabled: enabled && userId !== null && q.length > 0,
    placeholderData: keepPreviousData,
    staleTime: STALE_MS,
    retry,
    queryFn: ({ signal }) =>
      authed<SearchResponse>((headers) => searchLocal({ signal, headers, query: { q, scope, limit: 24 } })),
  });
}

/** `web` = the student asked for the web (Tavily first); otherwise the layered search routes by intent. */
export function useWebSearch(q: string, web: boolean, enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["search", "web", userId, web, q],
    enabled: enabled && userId !== null && q.length > 0,
    staleTime: STALE_MS,
    retry,
    queryFn: ({ signal }) => authed<WebResponse>((headers) => searchWeb({ signal, headers, query: { q, web } })),
  });
}
