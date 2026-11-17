import { type ReviewIn, type ReviewOut, reviewCard } from "@naadi/shared";
import type { OutboxRow } from "../db";
import { enqueue, outboxRows, registerEntity, type SendOutcome } from "../outbox";
import { offlineQueryClient } from "../runtime";
import { sendOp } from "../send";

/**
 * Entity `card_review`: one rating. Append-only and idempotent on `client_review_id` server-side (a replay never
 * reviews a card twice); the server schedules from `reviewed_at`, the moment the student rated. FIFO: a rating
 * never overtakes an older one of the same card (the server refuses a rating older than the card's last one).
 */
export interface CardReviewBody {
  cardId: string;
  review: ReviewIn;
}

export function sendCardReview(row: OutboxRow): Promise<SendOutcome> {
  const b = row.body as CardReviewBody;
  return sendOp<ReviewOut>(
    row.op_id,
    (headers) => reviewCard({ headers, path: { card_id: b.cardId }, body: b.review }),
    (out) => ({ kind: "ok", server: out }),
  );
}

registerEntity({
  entity: "card_review",
  coalesce: "append",
  send: sendCardReview,
  settled(row, outcome) {
    if (outcome.kind !== "ok") return;
    const qc = offlineQueryClient();
    void qc?.invalidateQueries({ queryKey: ["cards-stats", row.user_id] });
  },
});

export function queueCardReview(user: string, cardId: string, review: ReviewIn): Promise<OutboxRow | null> {
  return enqueue(user, {
    entity: "card_review",
    key: review.client_review_id,
    method: "POST",
    body: { cardId, review } satisfies CardReviewBody,
  });
}

export function queuedCardReviews(user: string): OutboxRow[] {
  return outboxRows(user, "card_review");
}
