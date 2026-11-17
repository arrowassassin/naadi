import type { PlanItemOut } from "@naadi/shared";
import type { Action } from "../today/format";

const MAX_BLOCK = 40;

export interface LinkContext {
  exam: string;
  /** The cards screen is switched on (feature flag): otherwise a cards item has no page to open. */
  cardsOn: boolean;
}

/**
 * What an item leads to inside Naadi: the Reader for a chapter, the Qbank builder prefilled (the block is built on
 * the server first) for question items, the card review. Outside resources, admin tasks and rest rows lead nowhere.
 * Same actions as Today's list, so one hook runs both.
 */
export function itemLink(item: PlanItemOut, ctx: LinkContext): Action | null {
  const count = Math.min(MAX_BLOCK, Math.max(1, item.target_count ?? MAX_BLOCK));
  switch (item.ref_type) {
    case "chapter":
      return item.ref_id ? { kind: "start_reading", chapterId: item.ref_id } : null;
    case "qbank":
      return { kind: "block", exam: item.ref_id ?? ctx.exam, count, status: "unused" };
    case "qbank_incorrect":
      return { kind: "block", exam: item.ref_id ?? ctx.exam, count, status: "incorrect" };
    case "weak_spot":
      return { kind: "weak_spot", exam: item.ref_id ?? ctx.exam, count };
    case "cards":
      return ctx.cardsOn ? { kind: "cards" } : null;
    default:
      return null;
  }
}
