import { type GeneratedMediaData, GeneratedMediaDataSchema } from "@naadi/shared";

/**
 * Canvas kind `generated_media` (segment 15b), shaped like S14's `CanvasKind` contract
 * (apps/web/src/canvas/registry.ts): register it with `registerCanvasKind(generatedMediaCanvasKind)` from
 * `canvas/kinds/index.ts`. The payload is only the item id and kind; the component fetches the item so the signed
 * URL is fresh and progress polling works while it is being made. The AI label chip is part of the component.
 */
export const generatedMediaSchema = GeneratedMediaDataSchema;
export type { GeneratedMediaData };

export const generatedMediaCanvasKind = {
  kind: "generated_media",
  schema: generatedMediaSchema,
  load: () => import("../../generation/GeneratedMediaCanvas"),
  label_key: "generation.canvas_title",
  icon: "sparkles",
  requires_flag: "generation",
} as const;
