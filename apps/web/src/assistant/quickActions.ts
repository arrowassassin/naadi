import type { AssistantAskKind } from "./ask";
import type { PageType } from "./protocol";

/**
 * Quick-action chips (Phone board) by page type. A chip's label is also the message it sends (both ui_copy),
 * with the ask kind the server answers it as. Pages without a type of their own get the general set.
 */
export interface QuickAction {
  key: string;
  ask: AssistantAskKind;
}

const GENERAL: QuickAction[] = [
  { key: "agent.chip.what_next", ask: "ask" },
  { key: "agent.chip.find_video", ask: "ask" },
  { key: "agent.chip.diagram", ask: "ask" },
];

const BY_TYPE: Partial<Record<PageType, QuickAction[]>> = {
  section: [
    { key: "agent.chip.summarise_section", ask: "summarise" },
    { key: "agent.chip.quiz_me", ask: "quiz" },
    { key: "agent.chip.flashcards", ask: "flashcards" },
    { key: "agent.chip.diagram", ask: "ask" },
  ],
  // A question page does not know (by design) whether the student answered: no chip invites the answer.
  // After the attempt the coaching card (AskNaadiCard) offers the explanation; the server checks either way.
  question: [{ key: "agent.chip.hint", ask: "hint" }],
  flashcard: [
    { key: "agent.chip.hint", ask: "hint" },
    { key: "agent.chip.explain_card", ask: "explain" },
  ],
  note: [
    { key: "agent.chip.summarise_note", ask: "summarise" },
    { key: "agent.chip.flashcards", ask: "flashcards" },
    { key: "agent.chip.quiz_me", ask: "quiz" },
  ],
  today: [
    { key: "agent.chip.what_next", ask: "ask" },
    { key: "agent.chip.weak_spots", ask: "ask" },
    { key: "agent.chip.find_video", ask: "ask" },
  ],
};

export function quickActionsFor(type: PageType | null | undefined): QuickAction[] {
  return (type ? BY_TYPE[type] : undefined) ?? GENERAL;
}
