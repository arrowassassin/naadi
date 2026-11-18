/**
 * Plan item status changes waiting for the server, as the planner sees them. Since segment 18 they live in the
 * device outbox (offline/entities/planItem.ts, IndexedDB `naadi-v1`, entity `plan_item`) with every other write.
 * Each keeps the time the student made it (`client_updated_at_ms`), and the server decides per item with
 * last-writer-wins, so a replay is harmless. Only the latest change per item is kept.
 */
import { useSyncExternalStore } from "react";
import { type ItemStatus, queuedPlanItems, queuePlanItem } from "../offline/entities/planItem";
import { dropRows, outboxPersists, outboxVersion, replay, subscribeOutbox } from "../offline/outbox";

export type { ItemStatus };

export interface QueuedStatus {
  id: string;
  status: ItemStatus;
  client_updated_at_ms: number;
}

export function readQueue(userId: string): QueuedStatus[] {
  return queuedPlanItems(userId)
    .filter((r) => r.state !== "failed")
    .map((r) => {
      const b = r.body as { id: string; status: ItemStatus };
      return { id: b.id, status: b.status, client_updated_at_ms: r.client_updated_at_ms };
    });
}

export function enqueueStatus(userId: string, item: QueuedStatus): Promise<unknown> {
  return queuePlanItem(userId, { id: item.id, status: item.status }, item.client_updated_at_ms);
}

export function dropStatus(userId: string, id: string, stamp: number): Promise<void> {
  // A newer change for the same item made meanwhile stays queued.
  return dropRows(
    queuedPlanItems(userId)
      .filter((r) => r.key === id && r.client_updated_at_ms <= stamp)
      .map((r) => r.op_id),
  );
}

/** Replay now (the outbox also replays on its own). Returns how many changes left the queue. */
export async function flushQueue(userId: string, _send?: unknown): Promise<number> {
  const before = readQueue(userId).length;
  await replay();
  return Math.max(0, before - readQueue(userId).length);
}

/** One-time move of the segment-16 localStorage queue into the outbox (then the key is deleted). */
export async function migrateLegacyPlanQueue(userId: string): Promise<number> {
  const key = `naadi.plan.outbox.v1:${userId}`;
  if (!(await outboxPersists())) return 0;
  let legacy: QueuedStatus[] = [];
  try {
    const raw = localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    legacy = Array.isArray(parsed) ? (parsed as QueuedStatus[]) : [];
  } catch {
    return 0;
  }
  let moved = 0;
  for (const q of legacy) {
    if (!q?.id || !q.status) continue;
    await queuePlanItem(userId, { id: q.id, status: q.status }, q.client_updated_at_ms);
    moved += 1;
  }
  try {
    localStorage.removeItem(key);
  } catch {
    // nothing to delete
  }
  return moved;
}

/** The ids of items whose latest change is still waiting to sync (the "Will sync" chip). */
export function usePendingIds(userId: string | null): ReadonlySet<string> {
  const v = useSyncExternalStore(subscribeOutbox, outboxVersion, outboxVersion);
  void v;
  return new Set(userId ? readQueue(userId).map((q) => q.id) : []);
}
