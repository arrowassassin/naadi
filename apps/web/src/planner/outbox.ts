/**
 * Plan item status changes made while the server could not be reached. Each keeps the time the student made it
 * (`client_updated_at_ms`), and the server decides per item with last-writer-wins: the newest stamp wins, an equal
 * or older one is ignored. Replaying one twice is therefore harmless. Only the latest change per item is kept. Kept
 * per user in localStorage; the full offline outbox (Dexie, every write kind) is segment 18, which takes these over
 * as the `plan_item` entity with the same request body.
 */
import { useSyncExternalStore } from "react";

export type ItemStatus = "planned" | "done" | "skipped";

export interface QueuedStatus {
  id: string;
  status: ItemStatus;
  client_updated_at_ms: number;
}

const key = (userId: string) => `naadi.plan.outbox.v1:${userId}`;
const MAX_QUEUED = 500;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export function readQueue(userId: string): QueuedStatus[] {
  try {
    const raw = localStorage.getItem(key(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedStatus[]) : [];
  } catch {
    return [];
  }
}

function writeQueue(userId: string, queue: QueuedStatus[]): void {
  try {
    if (queue.length === 0) localStorage.removeItem(key(userId));
    else localStorage.setItem(key(userId), JSON.stringify(queue.slice(-MAX_QUEUED)));
  } catch {
    // storage unavailable (private mode, quota): the change stays on screen until the page closes
  }
  emit();
}

export function enqueueStatus(userId: string, item: QueuedStatus): void {
  const queue = readQueue(userId).filter((q) => q.id !== item.id);
  queue.push(item);
  writeQueue(userId, queue);
}

export function dropStatus(userId: string, id: string, stamp: number): void {
  // A newer change for the same item made meanwhile stays queued.
  writeQueue(
    userId,
    readQueue(userId).filter((q) => !(q.id === id && q.client_updated_at_ms <= stamp)),
  );
}

export type SendOutcome = "sent" | "refused" | "unreachable";

/** Replay in order; a network failure stops the replay and keeps the rest. Returns how many the server answered. */
export async function flushQueue(userId: string, send: (item: QueuedStatus) => Promise<SendOutcome>): Promise<number> {
  let answered = 0;
  for (const item of readQueue(userId)) {
    const outcome = await send(item);
    if (outcome === "unreachable") break;
    answered += 1;
    dropStatus(userId, item.id, item.client_updated_at_ms);
  }
  return answered;
}

/** The ids of items whose latest change is still waiting to sync (the "Will sync" chip). */
export function usePendingIds(userId: string | null): ReadonlySet<string> {
  const snapshot = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
    () => (userId ? (localStorage.getItem(key(userId)) ?? "") : ""),
    () => "",
  );
  return new Set(snapshot ? (JSON.parse(snapshot) as QueuedStatus[]).map((q) => q.id) : []);
}
