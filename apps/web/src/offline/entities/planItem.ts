import { type ItemPatchOut, patchPlanItem } from "@naadi/shared";
import type { OutboxRow } from "../db";
import { enqueue, outboxRows, registerEntity, type SendOutcome, submit } from "../outbox";
import { offlineQueryClient } from "../runtime";
import { sendOp } from "../send";

/**
 * Entity `plan_item` (S16): a plan item's status. Last-writer-wins per item on `client_updated_at_ms` (strictly
 * newer wins, tie keeps the server's). Only the latest change per item is kept. When the server's row won
 * (`applied: false`, another device changed it later), the stored row is adopted and the item shows "Changed on
 * another device".
 */
export type ItemStatus = "planned" | "done" | "skipped";

export interface PlanItemBody {
  id: string;
  status: ItemStatus;
}

function send(row: OutboxRow): Promise<SendOutcome> {
  const b = row.body as PlanItemBody;
  return sendOp<ItemPatchOut>(
    row.op_id,
    (headers) =>
      patchPlanItem({
        headers,
        path: { item_id: b.id },
        body: { status: b.status, client_updated_at_ms: row.client_updated_at_ms },
      }),
    (out) => (out.applied === false ? { kind: "conflict", server: out } : { kind: "ok", server: out }),
  );
}

registerEntity({
  entity: "plan_item",
  coalesce: "replace",
  send,
  settled(row, outcome) {
    if (outcome.kind !== "ok" && outcome.kind !== "conflict") return;
    const qc = offlineQueryClient();
    void qc?.invalidateQueries({ queryKey: ["plan-week", row.user_id] });
    void qc?.invalidateQueries({ queryKey: ["today", row.user_id] });
  },
});

const draft = (b: PlanItemBody, stamp?: number) => ({
  entity: "plan_item" as const,
  key: b.id,
  method: "PATCH" as const,
  body: b,
  client_updated_at_ms: stamp,
});

export function queuePlanItem(user: string, b: PlanItemBody, stamp?: number) {
  return enqueue(user, draft(b, stamp));
}

export function submitPlanItem(user: string, b: PlanItemBody) {
  return submit(user, draft(b));
}

export function queuedPlanItems(user: string): OutboxRow[] {
  return outboxRows(user, "plan_item");
}
