import type { ReviewIn, ReviewOut } from "@naadi/shared";
import { ApiError } from "../auth/apiError";
import type { CardReviewBody } from "../offline/entities/cardReview";
import { discardRow, submit } from "../offline/outbox";

/**
 * A failure a later retry can fix: no connection, the server down or busy (5xx, 429), or a session that needs a
 * refresh (401). The rating is kept and replayed; anything else is an answer (a deleted card, a refused time).
 */
export function isRetryable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === null || e.status >= 500 || e.status === 429 || e.status === 401);
}

export type SubmitResult = { kind: "sent"; out: ReviewOut } | { kind: "queued" } | { kind: "refused"; error: unknown };

export interface SubmitDeps {
  /** Called after a rating is queued (refresh the count, start a replay). */
  queued?: () => void;
}

/**
 * Send one rating through the outbox (segment 18: every write does, online or not). It is tried at once unless
 * older ratings wait (FIFO: it never overtakes them, since the server refuses a rating older than the card's last
 * one). A refusal is shown on the review screen; its copy is kept in the device's conflict log for 7 days.
 */
export async function submitReview(
  userId: string,
  cardId: string,
  body: ReviewIn,
  deps: SubmitDeps = {},
): Promise<SubmitResult> {
  const { row, outcome } = await submit(userId, {
    entity: "card_review",
    key: body.client_review_id,
    method: "POST",
    body: { cardId, review: body } satisfies CardReviewBody,
  });
  if (outcome?.kind === "ok") return { kind: "sent", out: outcome.server as ReviewOut };
  if (outcome?.kind === "failed") {
    if (row) await discardRow(row.op_id);
    return { kind: "refused", error: new ApiError(outcome.code, 422, outcome.code, null) };
  }
  deps.queued?.();
  return { kind: "queued" };
}
