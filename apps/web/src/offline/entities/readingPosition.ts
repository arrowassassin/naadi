import { putReadingPosition, type ReadingPositionIn, type ReadingPositionWrite } from "@naadi/shared";
import type { OutboxRow } from "../db";
import { registerEntity, type SendOutcome, submit } from "../outbox";
import { sendOp } from "../send";

/**
 * Entity `reading_position`: where the student is in one section. Last-writer-wins per (user, section) on the
 * device's `client_updated_at` (S06; clamped server-side). Only the latest unsent position per section is kept.
 * The server answers with the stored position and `applied`; a position from another device that is newer wins
 * (no conflict notice: a scroll spot is not the student's work to lose).
 */
type Adopt = (res: ReadingPositionWrite) => void;
const adopters = new Set<Adopt>();

export function onPositionStored(fn: Adopt): () => void {
  adopters.add(fn);
  return () => {
    adopters.delete(fn);
  };
}

function send(row: OutboxRow): Promise<SendOutcome> {
  const body = row.body as ReadingPositionIn;
  return sendOp<ReadingPositionWrite>(
    row.op_id,
    (headers) => putReadingPosition({ headers, body }),
    (res) => ({ kind: "ok", server: res }),
  );
}

registerEntity({
  entity: "reading_position",
  coalesce: "replace",
  send,
  settled(_row, outcome) {
    if (outcome.kind === "ok" && outcome.server) for (const fn of adopters) fn(outcome.server as ReadingPositionWrite);
  },
});

export function savePosition(user: string, body: ReadingPositionIn) {
  return submit(user, {
    entity: "reading_position",
    key: body.section_id,
    method: "PUT",
    body,
    client_updated_at_ms: Date.parse(body.client_updated_at ?? "") || undefined,
  });
}
