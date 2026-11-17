import type { ReviewIn, ReviewOut } from "@naadi/shared";
import { ApiError } from "../auth/apiError";
import { enqueueReview, readQueue } from "./outbox";

/**
 * A failure a later retry can fix: no connection, the server down or busy (5xx, 429), or a session that needs a
 * refresh (401). The rating is kept and replayed; anything else is an answer (a deleted card, a refused time).
 */
export function isRetryable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === null || e.status >= 500 || e.status === 429 || e.status === 401);
}

export type SubmitResult = { kind: "sent"; out: ReviewOut } | { kind: "queued" } | { kind: "refused"; error: unknown };

export interface SubmitDeps {
  send: (cardId: string, body: ReviewIn) => Promise<ReviewOut>;
  online: () => boolean;
  /** Called after a rating is queued (refresh the count, start a replay). */
  queued?: () => void;
}

/**
 * Send one rating, or queue it behind ratings already waiting. Reads the queue at the moment of sending (not a
 * rendered count): a rating made while the previous one was failing must queue behind it, never overtake it,
 * since the server refuses a rating older than the card's last one.
 */
export async function submitReview(
  userId: string,
  cardId: string,
  body: ReviewIn,
  deps: SubmitDeps,
): Promise<SubmitResult> {
  if (readQueue(userId).length > 0 || !deps.online()) {
    enqueueReview(userId, { cardId, body });
    deps.queued?.();
    return { kind: "queued" };
  }
  try {
    return { kind: "sent", out: await deps.send(cardId, body) };
  } catch (error) {
    if (!isRetryable(error)) return { kind: "refused", error };
    enqueueReview(userId, { cardId, body });
    deps.queued?.();
    return { kind: "queued" };
  }
}
