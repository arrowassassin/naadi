import type { AttemptIn, AttemptOut } from "@naadi/shared";

/**
 * Answers submitted while offline. Each is kept on this device (per user) and replayed when the connection
 * returns; the server grades and is idempotent per (block, question), so a replay can never double-count.
 * The full offline outbox (Dexie, every write kind) is segment 18; this holds only practice answers until then.
 */
export interface QueuedAttempt {
  blockId: string;
  body: AttemptIn;
  queuedAt: number;
}

const key = (userId: string) => `naadi.practice.outbox.v1:${userId}`;
const MAX_QUEUED = 400;

export function readQueue(userId: string): QueuedAttempt[] {
  try {
    const raw = localStorage.getItem(key(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedAttempt[]) : [];
  } catch {
    return [];
  }
}

function writeQueue(userId: string, queue: QueuedAttempt[]): void {
  try {
    if (queue.length === 0) localStorage.removeItem(key(userId));
    else localStorage.setItem(key(userId), JSON.stringify(queue.slice(-MAX_QUEUED)));
  } catch {
    // storage unavailable (private mode, quota): the answer is still shown as chosen until the page closes
  }
}

/** Queue one answer; answering the same question again replaces the earlier entry (the first stands server-side). */
export function enqueueAttempt(userId: string, blockId: string, body: AttemptIn): void {
  const queue = readQueue(userId).filter((q) => !(q.blockId === blockId && q.body.question_id === body.question_id));
  queue.push({ blockId, body, queuedAt: Date.now() });
  writeQueue(userId, queue);
}

export function queuedFor(userId: string, blockId: string, questionId: string): QueuedAttempt | undefined {
  return readQueue(userId).find((q) => q.blockId === blockId && q.body.question_id === questionId);
}

/**
 * Replay the queue in order. An entry leaves the queue when the server answered it (graded, or refused for a
 * reason a retry cannot fix, such as a block that no longer exists); a network failure stops the replay and
 * keeps the rest. Returns how many were sent.
 */
export async function flushQueue(
  userId: string,
  send: (blockId: string, body: AttemptIn) => Promise<AttemptOut>,
  isUnreachable: (e: unknown) => boolean,
  onSent?: (blockId: string, out: AttemptOut) => void,
): Promise<number> {
  let sent = 0;
  for (const item of readQueue(userId)) {
    try {
      const out = await send(item.blockId, item.body);
      onSent?.(item.blockId, out);
      sent += 1;
    } catch (err) {
      if (isUnreachable(err)) break;
    }
    writeQueue(
      userId,
      readQueue(userId).filter((q) => !(q.blockId === item.blockId && q.body.question_id === item.body.question_id)),
    );
  }
  return sent;
}
