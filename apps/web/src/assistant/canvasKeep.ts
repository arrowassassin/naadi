import { useEffect } from "react";
import type { CanvasRef } from "../canvas/registry";
import { registerCanvasKeep } from "../canvas/registry";
import { apiTransport } from "../notes/api";

/**
 * "Keep" on an assistant document in the canvas: the draft becomes the student's own note through the notes API
 * (segment 13), anchored where the assistant drafted it (a section keeps one page note: the server returns it and
 * the draft is appended, never lost). Registered while the assistant surface is mounted.
 */
export function useCanvasKeepNotes(): void {
  useEffect(
    () =>
      registerCanvasKeep(async (item: CanvasRef) => {
        const data = item.data as { doc?: unknown; anchor?: { type?: string; id?: string } | null };
        const anchor = data.anchor;
        const anchored = anchor?.id && ["section", "question", "video"].includes(anchor.type ?? "");
        const id = crypto.randomUUID();
        const result = await apiTransport.create(id, {
          kind: "note",
          canvas: "paper",
          body: data.doc,
          anchor_type: anchored ? anchor?.type : "free",
          anchor_id: anchored ? anchor?.id : null,
        });
        if (result !== "ok") throw new Error(typeof result === "string" ? result : "note_refused");
        return { type: "note", id };
      }),
    [],
  );
}
