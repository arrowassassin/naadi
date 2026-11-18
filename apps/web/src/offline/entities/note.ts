import { apiTransport } from "../../notes/api";
import { flushOps, loadOps } from "../../notes/outbox";
import { registerEntity } from "../outbox";

/**
 * Entity `note` (notes and highlights, S13): a delegated entity. Its rows live in the outbox; S13's flushOps keeps
 * its rules (idempotent create by the device-made id, field patches folded per note, a create+delete never sent,
 * refusals kept on screen) and is what the scheduler runs. Notes filed in a notebook made offline wait for it.
 */
registerEntity({
  entity: "note",
  coalesce: "replace",
  after: ["notebook"],
  async flush(user) {
    await loadOps(user);
    await flushOps(user, apiTransport);
  },
});
