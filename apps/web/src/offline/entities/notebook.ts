import { notebookSync } from "../../notebooks/api";
import { registerEntity } from "../outbox";
import { offlineQueryClient } from "../runtime";

/**
 * Entity `notebook` (notebooks, their pages and page order, S13): a delegated entity. Its rows live in the outbox;
 * S13's NotebookSync keeps its rules (folding, a delete cancelling what is queued for its target, strict order:
 * a page exists before its patch) and is what the scheduler runs.
 */
registerEntity({
  entity: "notebook",
  coalesce: "append",
  async flush(user) {
    const qc = offlineQueryClient();
    if (!qc) return;
    await notebookSync(user, qc).flush();
  },
});
