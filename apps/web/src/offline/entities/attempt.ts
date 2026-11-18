import { type AttemptIn, type AttemptOut, createAttempt } from "@naadi/shared";
import type { OutboxRow } from "../db";
import { enqueue, outboxRows, registerEntity, rowsFor, type SendOutcome, submit } from "../outbox";
import { sendOp } from "../send";

/**
 * Entity `attempt`: one answer to one question of a block. The server grades it and the first answer stands
 * (idempotent per (block, question)); only the latest unsent choice per question is kept on the device. A replay
 * after the section's deadline is refused (409 `section_ended` / `finished`): it is parked with code `time_up`,
 * shown as "Not counted: time was up", never retried and never dropped silently.
 *
 * The body carries no answer field: the device only knows the student's choice until the server grades it, and the
 * explanation is fetched from the server after the attempt synced (tutor mode offline: "Explanation after you
 * reconnect").
 */
export interface AttemptBody {
  blockId: string;
  attempt: AttemptIn;
}

export const attemptKey = (blockId: string, questionId: string) => `${blockId}:${questionId}`;

type Applied = (blockId: string, out: AttemptOut) => void;
const appliedListeners = new Set<Applied>();

/** Screens that show the graded answer subscribe here (the practice/exam pages adopt it into their queries). */
export function onAttemptApplied(fn: Applied): () => void {
  appliedListeners.add(fn);
  return () => {
    appliedListeners.delete(fn);
  };
}

const TIME_UP = new Set(["section_ended", "finished"]);

export function sendAttempt(row: OutboxRow): Promise<SendOutcome> {
  const b = row.body as AttemptBody;
  return sendOp<AttemptOut>(
    row.op_id,
    (headers) => createAttempt({ headers, path: { block_id: b.blockId }, body: b.attempt }),
    (out) => ({ kind: "ok", server: out }),
    (err) => (err.status === 409 && err.code && TIME_UP.has(err.code) ? { kind: "failed", code: "time_up" } : null),
  );
}

registerEntity({
  entity: "attempt",
  coalesce: "replace",
  send: sendAttempt,
  settled(row, outcome) {
    if (outcome.kind !== "ok" || !outcome.server) return;
    const b = row.body as AttemptBody;
    for (const fn of appliedListeners) fn(b.blockId, outcome.server as AttemptOut);
  },
});

export function queueAttempt(user: string, blockId: string, attempt: AttemptIn): Promise<OutboxRow | null> {
  return enqueue(user, {
    entity: "attempt",
    key: attemptKey(blockId, attempt.question_id),
    method: "POST",
    body: { blockId, attempt } satisfies AttemptBody,
  });
}

/** The newest queued (or parked) answer to one question. */
export function queuedAttempt(user: string, blockId: string, questionId: string): OutboxRow | undefined {
  return rowsFor(user, "attempt", attemptKey(blockId, questionId)).at(-1);
}

export function queuedAttempts(user: string): OutboxRow[] {
  return outboxRows(user, "attempt");
}

/** Answer now through the outbox (online: graded at once; offline: queued). */
export function submitAttempt(user: string, blockId: string, attempt: AttemptIn) {
  return submit(user, {
    entity: "attempt",
    key: attemptKey(blockId, attempt.question_id),
    method: "POST",
    body: { blockId, attempt } satisfies AttemptBody,
  });
}
