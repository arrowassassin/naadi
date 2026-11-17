import type { QuestionReviewIn, ReviewItemDetail } from "@naadi/shared";

export type Ratings = Partial<QuestionReviewIn["ratings"]>;

/** True once every Part C rating the server requires is set. */
export function ratingsComplete(r: Ratings): r is QuestionReviewIn["ratings"] {
  return (
    r.accuracy !== undefined &&
    r.single_best_answer !== undefined &&
    r.cognitive_level !== undefined &&
    r.difficulty !== undefined &&
    r.taxonomy_ok !== undefined
  );
}

/** An approval the ratings contradict: an incorrect item, no single best answer, or wrong taxonomy. */
export function contradictsApproval(r: Ratings): boolean {
  return r.accuracy === "incorrect" || r.single_best_answer === false || r.taxonomy_ok === false;
}

/** Each ui_copy key of `review.blocked.<code>` explains one gate blocker; returns the key of a code. */
export function blockerKey(code: string): string {
  return `review.blocked.${code}`;
}

/**
 * Why Approve is not available now, as a ui_copy key (null: it is). The server enforces every one of these;
 * the page says so before the click.
 */
export function approveBlockedKey(item: ReviewItemDetail, r: Ratings): string | null {
  if (!item.can_review.allowed) return `review.cannot.${item.can_review.reason ?? "status_not_reviewable"}`;
  if (item.gates.blockers.length > 0) return "review.approve_blocked_gates";
  if (!ratingsComplete(r)) return "review.approve_blocked_ratings";
  if (contradictsApproval(r)) return "review.approve_blocked_contradiction";
  return null;
}

/** Revise and Reject need the same complete ratings (the server requires them for every decision). */
export function decisionBlockedKey(item: ReviewItemDetail, r: Ratings): string | null {
  if (!item.can_review.allowed) return `review.cannot.${item.can_review.reason ?? "status_not_reviewable"}`;
  if (!ratingsComplete(r)) return "review.decide_blocked_ratings";
  return null;
}

export type OutcomeKey = "recorded" | "published" | "revise" | "rejected" | "adjudication";

/** The queue row's next-to-review test: waiting for a decision and not yet reviewed by this admin. */
export function awaitingMine(row: { state: string; mine_submitted: boolean }): boolean {
  return (row.state === "awaiting" || row.state === "adjudication") && !row.mine_submitted;
}

export function isSeedSpot(item: Pick<ReviewItemDetail, "origin" | "status">): boolean {
  return item.origin === "seed" && item.status === "published";
}
