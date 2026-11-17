import type { QueryClient } from "@tanstack/react-query";

/**
 * What entity handlers need from the running app: the QueryClient (to adopt the server's answer into screens).
 * Set once by boot.ts; handlers work without it (tests, before boot) and the next query fetch shows the result.
 */
let client: QueryClient | null = null;

export function setOfflineQueryClient(qc: QueryClient | null): void {
  client = qc;
}

export function offlineQueryClient(): QueryClient | null {
  return client;
}

/**
 * Query options for a read with a device copy (segment 18's `offlineFirst` helper): TanStack Query pauses queries
 * while the browser is offline by default, so a screen opened offline would wait forever; with this the query runs
 * once, the network fails at once, and the read returns the copy from `naadi-v1` (Download this week or the last
 * good read).
 */
export const OFFLINE_FIRST = { networkMode: "offlineFirst" } as const;
