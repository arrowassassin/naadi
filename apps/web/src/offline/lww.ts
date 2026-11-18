/**
 * Last-writer-wins per row, the device's half of `naadi_api/sync/lww.py` (same rule, same golden vectors in
 * packages/shared/fixtures/lww_vectors.json, run by both test suites):
 *
 * - The incoming stamp is clamped to the server's clock + 5 minutes (a device whose clock runs ahead cannot win
 *   every race for the next hours).
 * - It wins only when strictly newer than both the stored row and any tombstone of that row; a tie keeps the
 *   server's version.
 * - A delete is a tombstone op: it removes the row when it wins, and a later edit with a newer stamp brings the
 *   row back; an older edit replayed after the delete loses, on every device the same way.
 *
 * The client uses it to predict nothing: the server's answer (the stored row + `applied`) is always adopted.
 * It exists so the rule is written down once per language and proven equal.
 */
export const CLAMP_AHEAD_MS = 5 * 60 * 1000;

export interface LwwInput {
  stored_ms: number | null;
  tombstone_ms: number | null;
  incoming_ms: number;
  server_now_ms: number;
  is_delete: boolean;
}

export interface LwwDecision {
  applied: boolean;
  /** The stamp the row (or tombstone) carries afterwards. */
  effective_ms: number | null;
}

export function decide(i: LwwInput): LwwDecision {
  const incoming = Math.min(i.incoming_ms, i.server_now_ms + CLAMP_AHEAD_MS);
  const marks = [i.stored_ms, i.tombstone_ms].filter((m): m is number => m !== null);
  const current = marks.length ? Math.max(...marks) : null;
  if (current !== null && incoming <= current) return { applied: false, effective_ms: current };
  // A delete of a row that does not exist (and was never deleted) still writes its tombstone: an older edit
  // replayed later must not create the row.
  return { applied: true, effective_ms: incoming };
}
