import { Button } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { Layers, Plus } from "lucide-react";
import { useState } from "react";
import { useMe } from "../auth/api";
import { useCardStats } from "./api";
import { CardDialog } from "./CardDialog";
import { useCardsCopy } from "./copy";

/**
 * Entry points other screens mount (segment 08: the question view and the Builder). Each renders nothing while the
 * `cards` feature flag is off, so no screen ever links to a missing feature.
 */

function useCardsOn(): boolean {
  return useMe().data?.features.cards === true;
}

export interface SaveAsFlashcardProps {
  questionId: string;
  /** Text the dialog starts with; the student edits it before saving. */
  prefill?: { front: string; back: string };
  /**
   * The student's attempt on this question is graded in this view (the explanation is showing). The button is
   * absent before that; the server refuses a question link without a graded attempt anyway.
   */
  graded: boolean;
  className?: string;
}

/** "Save as flashcard" for the question view, after the student's graded attempt. */
export function SaveAsFlashcard({ questionId, prefill, graded, className }: SaveAsFlashcardProps) {
  const t = useCardsCopy();
  const on = useCardsOn();
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  if (!on || !graded) return null;
  return (
    <>
      <Button
        variant="secondary"
        icon={<Plus size={18} />}
        onClick={() => setOpen(true)}
        className={className}
        aria-describedby={saved ? `saved-${questionId}` : undefined}
      >
        {t("cards.save_as")}
      </Button>
      {saved ? (
        <span id={`saved-${questionId}`} role="status" className="text-text-2 text-ui-sm">
          {t("cards.save_as_done")}
        </span>
      ) : null}
      <CardDialog
        open={open}
        draft={{ front_text: prefill?.front ?? "", back_text: prefill?.back ?? "", question_id: questionId }}
        onClose={() => setOpen(false)}
        onSaved={() => setSaved(true)}
      />
    </>
  );
}

/** "Flashcards · N due" for the Builder: due today in the student's own day, from GET /api/cards/stats. */
export function CardsDueLink({ className }: { className?: string }) {
  const t = useCardsCopy();
  const on = useCardsOn();
  const stats = useCardStats(on);
  if (!on) return null;
  const n = stats.data?.due_today;
  return (
    <Link
      to="/cards"
      className={
        className ??
        "inline-flex min-h-11 items-center gap-2 rounded-pill border border-line-control px-4 text-text text-ui-sm no-underline hover:bg-surface-2 hover:text-text"
      }
    >
      <Layers aria-hidden="true" size={16} />
      {n === undefined ? t("cards.due_link_loading") : n > 0 ? t("cards.due_link", { n }) : t("cards.due_link_none")}
    </Link>
  );
}
