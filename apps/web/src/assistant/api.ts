import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { z } from "zod";
import { authed } from "../auth/api";
import type { SdkResult } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { type Memories, type ThreadItem, zMemories, zThread, zThreadList } from "./protocol";

/*
 * /api/assistant REST (docs/segments/14-protocol.md): threads (list, rename, delete) and memories. The routes are
 * not in the generated SDK yet (the server lands them in the same segment), so this calls them with fetch and
 * validates every response with the protocol's zod schemas; `authed` adds the token and refreshes it once.
 * Raw fetch: no generated SDK method exists for these routes yet.
 */

async function call<T>(
  method: "GET" | "PATCH" | "DELETE" | "POST",
  path: string,
  headers: Record<string, string>,
  schema: z.ZodType<T> | null,
  body?: unknown,
  signal?: AbortSignal,
): Promise<SdkResult<T>> {
  const res = await fetch(path, {
    method,
    signal,
    headers: { ...headers, ...(body === undefined ? {} : { "content-type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = res.status === 204 ? "" : await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  if (!res.ok) return { error: json, response: res };
  // No-content answers (204) resolve with null: unwrap treats `undefined` as "no data".
  if (!schema) return { data: null as T, response: res };
  const parsed = schema.safeParse(json);
  if (!parsed.success) return { error: parsed.error, response: res };
  return { data: parsed.data, response: res };
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const threadsKey = (userId: string | null, q: string) => ["assistant", "threads", userId, q] as const;
export const memoriesKey = (userId: string | null) => ["assistant", "memories", userId] as const;

export function useThreads(q: string, enabled = true) {
  const userId = useUserId();
  const query = q.trim();
  return useQuery({
    queryKey: threadsKey(userId, query),
    enabled: enabled && userId !== null,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ limit: "50" });
      if (query) params.set("q", query);
      return authed((h) => call("GET", `/api/assistant/threads?${params}`, h, zThreadList, undefined, signal));
    },
  });
}

export function useRenameThread() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      authed<ThreadItem>((h) =>
        call("PATCH", `/api/assistant/threads/${encodeURIComponent(id)}`, h, zThread, { title }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assistant", "threads"] }),
  });
}

export function useDeleteThread() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      authed<null>((h) => call("DELETE", `/api/assistant/threads/${encodeURIComponent(id)}`, h, null)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assistant", "threads"] }),
  });
}

/** After a turn the server has written or touched the thread's row: refresh the list. */
export function useInvalidateThreads(): () => void {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ queryKey: ["assistant", "threads"] });
}

export function useMemories(enabled = true) {
  const userId = useUserId();
  return useQuery({
    queryKey: memoriesKey(userId),
    enabled: enabled && userId !== null,
    queryFn: ({ signal }) =>
      authed<Memories>((h) => call("GET", "/api/assistant/memories", h, zMemories, undefined, signal)),
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  const userId = useUserId();
  return useMutation({
    mutationFn: (key: string) =>
      authed<null>((h) => call("DELETE", `/api/assistant/memories/${encodeURIComponent(key)}`, h, null)),
    onSuccess: (_d, key) => {
      qc.setQueryData<Memories>(memoriesKey(userId), (m) =>
        m ? { ...m, items: m.items.filter((x) => x.key !== key) } : m,
      );
      void qc.invalidateQueries({ queryKey: memoriesKey(userId) });
    },
  });
}

export function useDecideSuggestion() {
  const qc = useQueryClient();
  const userId = useUserId();
  return useMutation({
    mutationFn: ({ key, decision }: { key: string; decision: "keep" | "discard" }) =>
      authed<null>((h) =>
        call("POST", `/api/assistant/memories/suggestions/${encodeURIComponent(key)}`, h, null, { decision }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: memoriesKey(userId) }),
  });
}
