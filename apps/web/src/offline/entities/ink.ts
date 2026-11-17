import { InkSurfaceTypeSchema } from "@naadi/shared";
import { serverNow } from "../../ink/persist/clock";
import { dexieStore } from "../../ink/persist/db";
import { InkSync } from "../../ink/persist/sync";
import { apiTransport, classifyInkError } from "../../ink/persist/transport";
import { outboxRows, registerEntity } from "../outbox";

/**
 * Entity `ink` (S12b): a delegated entity, one outbox row per pending item, grouped by surface. A surface on
 * screen syncs through its own InkSync (useInkLayer). Ink drawn on a surface the student has since left (another
 * section, another card) is sent here by a headless InkSync of that surface: the same rules (merge by item id with
 * mergeInkItems, the server's clamped copy adopted, invalid items quarantined, never dropped), with no canvas.
 * Sending twice is harmless: the server's merge is idempotent per item version.
 */
async function flushSurface(user: string, surface: string): Promise<void> {
  const [type, ...rest] = surface.split(":");
  const parsed = InkSurfaceTypeSchema.safeParse(type);
  if (!parsed.success || rest.length === 0) return;
  const sync = new InkSync({
    transport: apiTransport(parsed.data, rest.join(":")),
    store: dexieStore(user, surface),
    classify: classifyInkError,
    now: serverNow,
    onRemote: () => undefined, // no canvas: the merged layer is written to the device snapshot
    setTimer: () => 0,
    clearTimer: () => undefined,
  });
  try {
    await sync.restore();
    await sync.refresh();
  } finally {
    sync.stop();
  }
}

registerEntity({
  entity: "ink",
  coalesce: "replace",
  async flush(user) {
    const surfaces = new Set(
      outboxRows(user, "ink")
        .filter((r) => r.state === "queued")
        .map((r) => r.group),
    );
    for (const surface of surfaces) await flushSurface(user, surface);
  },
});
