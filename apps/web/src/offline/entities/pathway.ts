import { apiTransport } from "../../pathway/api";
import { flushOps, loadOps } from "../../pathway/outbox";
import { registerEntity } from "../outbox";

/**
 * Entities `user_pathway_task` and `cost` (S17): delegated entities. Their rows live in the outbox; S17's flushOps
 * keeps its rules (fields folded per row, a cost created and deleted before any request never sent, refusals kept on
 * screen) and is what the scheduler runs. Both entities share one ordered queue, so one flush serves both. Vault
 * uploads and opens are never queued.
 */
async function flush(user: string): Promise<void> {
  await loadOps(user);
  await flushOps(user, apiTransport);
}

registerEntity({ entity: "user_pathway_task", coalesce: "replace", flush });
registerEntity({ entity: "cost", coalesce: "replace", after: ["user_pathway_task"], flush });
