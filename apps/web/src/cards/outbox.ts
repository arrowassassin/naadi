import type { ReviewIn } from "@naadi/shared";

/**
 * Ratings made while the server could not be reached. Each keeps its own `client_review_id` and `reviewed_at`,
 * so a replay schedules from the time the student rated, and the server is idempotent per review id: sending
 * one twice can never review a card twice. Kept per user in localStorage, replayed in order. The full offline
 * outbox (Dexie, every write kind) is segment 18; it takes these over by the same request body.
 */
export interface QueuedReview {
  cardId: string;
  body: ReviewIn;
}

const key = (userId: string) => `naadi.cards.outbox.v1:${userId}`;
const MAX_QUEUED = 1000;

export function readQueue(userId: string): QueuedReview[] {
  try {
    const raw = localStorage.getItem(key(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedReview[]) : [];
  } catch {
    return [];
  }
}

function writeQueue(userId: string, queue: QueuedReview[]): void {
  try {
    if (queue.length === 0) localStorage.removeItem(key(userId));
    else localStorage.setItem(key(userId), JSON.stringify(queue.slice(-MAX_QUEUED)));
  } catch {
    // storage unavailable (private mode, quota): the rating stays in memory until the page closes
  }
}

export function enqueueReview(userId: string, item: QueuedReview): void {
  const queue = readQueue(userId).filter((q) => q.body.client_review_id !== item.body.client_review_id);
  queue.push(item);
  writeQueue(userId, queue);
}

export function dropReview(userId: string, clientReviewId: string): void {
  writeQueue(
    userId,
    readQueue(userId).filter((q) => q.body.client_review_id !== clientReviewId),
  );
}

export type SendOutcome = "sent" | "refused" | "unreachable";

/**
 * Replay the queue in order. An entry leaves the queue once the server answered it (scheduled, replayed, or refused
 * for a reason a retry cannot fix, such as a card that was deleted); a network failure stops the replay and keeps
 * the rest, in order. Returns how many the server accepted.
 */
export async function flushQueue(userId: string, send: (item: QueuedReview) => Promise<SendOutcome>): Promise<number> {
  let sent = 0;
  for (const item of readQueue(userId)) {
    const outcome = await send(item);
    if (outcome === "unreachable") break;
    if (outcome === "sent") sent += 1;
    dropReview(userId, item.body.client_review_id);
  }
  return sent;
}
