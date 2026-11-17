import type { AttemptIn, AttemptOut } from "@naadi/shared";
import type { OutboxRow } from "../offline/db";
import { onAttemptApplied, queueAttempt, queuedAttempt, queuedAttempts } from "../offline/entities/attempt";
import { outboxPersists, replay } from "../offline/outbox";

/**
 * Practice answers waiting for the server, as the practice and exam screens see them. Since segment 18 they live
 * in the device outbox (offline/entities/attempt.ts, IndexedDB `naadi-v1`) with every other write; this module keeps
 * the screens' small API. The server grades and is idempotent per (block, question), so a replay can never
 * double-count; an answer refused because the section's time was up stays visible (`failed: "time_up"`).
 */
export interface QueuedAttempt {
  blockId: string;
  body: AttemptIn;
  queuedAt: number;
  /** Parked by the server's answer (e.g. `time_up`); null while it waits to be sent. */
  failed: string | null;
}

function toQueued(row: OutboxRow): QueuedAttempt {
  const b = row.body as { blockId: string; attempt: AttemptIn };
  return {
    blockId: b.blockId,
    body: b.attempt,
    queuedAt: row.queued_at,
    failed: row.state === "failed" ? row.last_error : null,
  };
}

export function readQueue(userId: string): QueuedAttempt[] {
  return queuedAttempts(userId).map(toQueued);
}

/** Queue one answer; answering the same question again replaces the earlier unsent one (the first stands server-side). */
export function enqueueAttempt(userId: string, blockId: string, body: AttemptIn): void {
  void queueAttempt(userId, blockId, body);
}

export function queuedFor(userId: string, blockId: string, questionId: string): QueuedAttempt | undefined {
  const row = queuedAttempt(userId, blockId, questionId);
  return row ? toQueued(row) : undefined;
}

/**
 * Replay now (the outbox also replays on its own: online, visible again, every 30 s). `onSent` sees each answer the
 * server graded during this replay. Returns how many answers left the queue.
 */
export async function flushQueue(
  userId: string,
  _send?: unknown,
  _isUnreachable?: unknown,
  onSent?: (blockId: string, out: AttemptOut) => void,
): Promise<number> {
  const before = queuedAttempts(userId).filter((r) => r.state !== "failed").length;
  const off = onSent ? onAttemptApplied(onSent) : () => {};
  try {
    await replay();
  } finally {
    off();
  }
  return Math.max(0, before - queuedAttempts(userId).filter((r) => r.state !== "failed").length);
}

/** One-time move of the pre-segment-18 localStorage queue into the outbox (then the key is deleted). */
export async function migrateLegacyPracticeQueue(userId: string): Promise<number> {
  const key = `naadi.practice.outbox.v1:${userId}`;
  if (!(await outboxPersists())) return 0; // keep the old queue until the outbox can hold it across reloads
  let legacy: { blockId: string; body: AttemptIn }[] = [];
  try {
    const raw = localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    legacy = Array.isArray(parsed) ? (parsed as typeof legacy) : [];
  } catch {
    return 0;
  }
  let moved = 0;
  for (const item of legacy) {
    if (!item?.blockId || !item.body?.question_id) continue;
    await queueAttempt(userId, item.blockId, item.body);
    moved += 1;
  }
  try {
    localStorage.removeItem(key);
  } catch {
    // storage unavailable: nothing to delete
  }
  return moved;
}
