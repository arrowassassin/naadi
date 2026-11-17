/**
 * Notebook covers: the fixed set of colour token names the API accepts (`notebooks.cover`, migration 0015), each
 * drawn with token classes only (design/Notebook.dc.html: coloured cover, a darker spine, text in the role's ink).
 */
export const COVERS = ["lime", "violet", "amber", "paper", "surface-2", "ink-violet"] as const;
export type Cover = (typeof COVERS)[number];

const CLASSES: Record<Cover, { cover: string; swatch: string }> = {
  lime: { cover: "bg-lime text-lime-ink border-l-lime-ink", swatch: "bg-lime text-lime-ink" },
  violet: { cover: "bg-violet text-violet-ink border-l-violet-ink", swatch: "bg-violet text-violet-ink" },
  amber: { cover: "bg-amber text-amber-ink border-l-amber-ink", swatch: "bg-amber text-amber-ink" },
  paper: { cover: "bg-paper text-ground border-l-ground", swatch: "bg-paper text-ground" },
  "surface-2": { cover: "bg-surface-2 text-text border-l-lime", swatch: "bg-surface-2 text-text" },
  "ink-violet": { cover: "bg-ink-violet text-paper border-l-violet-ink", swatch: "bg-ink-violet text-paper" },
};

export function isCover(v: unknown): v is Cover {
  return typeof v === "string" && (COVERS as readonly string[]).includes(v);
}

/** Token classes for a cover; an unset cover is the dark surface one. */
export function coverClass(cover: string | null | undefined) {
  return CLASSES[isCover(cover) ? cover : "surface-2"];
}

/** ui_copy key of a cover's name (keys are dotted lowercase with underscores). */
export function coverKey(cover: Cover): string {
  return `notebooks.cover.${cover.replace("-", "_")}`;
}
