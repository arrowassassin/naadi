import type { ReviewIn } from "@naadi/shared";
import { queueCardReview, queuedCardReviews } from "../offline/entities/cardReview";
import { dropRows, outboxPersists, replay } from "../offline/outbox";

/**
 * Ratings waiting for the server, as the review screen sees them. Since segment 18 they live in the device outbox
 * (offline/entities/cardReview.ts, IndexedDB `naadi-v1`). Each keeps its own `client_review_id` and `reviewed_at`,
 * so a replay schedules from the time the student rated, and the server is idempotent per review id. A rating the
 * server refuses is parked (visible in the sync sheet), never dropped silently.
 */
export interface QueuedReview {
  cardId: string;
  body: ReviewIn;
}

/** Ratings still to send (parked ones are counted by the sync sheet, not here: they no longer block the queue). */
export function readQueue(userId: string): QueuedReview[] {
  return queuedCardReviews(userId)
    .filter((r) => r.state !== "failed")
    .map((r) => {
      const b = r.body as { cardId: string; review: ReviewIn };
      return { cardId: b.cardId, body: b.review };
    });
}

export function enqueueReview(userId: string, item: QueuedReview): void {
  void queueCardReview(userId, item.cardId, item.body);
}

export function dropReview(userId: string, clientReviewId: string): void {
  void dropRows(
    queuedCardReviews(userId)
      .filter((r) => r.key === clientReviewId)
      .map((r) => r.op_id),
  );
}

export type SendOutcome = "sent" | "refused" | "unreachable";

/** Replay now (the outbox also replays on its own). Returns how many ratings left the queue. */
export async function flushQueue(userId: string, _send?: unknown): Promise<number> {
  const before = readQueue(userId).length;
  await replay();
  return Math.max(0, before - readQueue(userId).length);
}

/** One-time move of the pre-segment-18 localStorage queue into the outbox (then the key is deleted). */
export async function migrateLegacyCardQueue(userId: string): Promise<number> {
  const key = `naadi.cards.outbox.v1:${userId}`;
  if (!(await outboxPersists())) return 0;
  let legacy: QueuedReview[] = [];
  try {
    const raw = localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    legacy = Array.isArray(parsed) ? (parsed as QueuedReview[]) : [];
  } catch {
    return 0;
  }
  let moved = 0;
  for (const item of legacy) {
    if (!item?.cardId || !item.body?.client_review_id) continue;
    await queueCardReview(userId, item.cardId, item.body);
    moved += 1;
  }
  try {
    localStorage.removeItem(key);
  } catch {
    // nothing to delete
  }
  return moved;
}
