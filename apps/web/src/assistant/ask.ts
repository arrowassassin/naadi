import { useSyncExternalStore } from "react";

/**
 * How a page hands a question to the assistant (segment 14) without importing it. The assistant registers one
 * handler while it can answer (the `assistant` feature is on and no exam-mode session is active); pages show
 * their "ask" affordances only while a handler is registered (`useAssistantAvailable()`), so a button never
 * does nothing.
 *
 * - `kind: "ask"` without `draft` opens the panel focused, with the context attached as a chip; the student
 *   types the question. With `draft`, the draft is put in the input (still not sent).
 * - Every other kind sends at once ("hint", "explain", "summarise", "flashcards", "quiz").
 * - Context carries ids only; the server reads the thing through its own API (attempt gate included). A
 *   question context never says whether it was answered: the server checks the attempts.
 */
export type AssistantAskKind = "hint" | "explain" | "ask" | "summarise" | "flashcards" | "quiz";

export type AssistantSurfaceType = "section" | "note" | "question";

export type AssistantAskContext =
  | { type: "flashcard"; id: string }
  | { type: "section"; id: string; chapter_id: string }
  | { type: "selection"; surface: { type: AssistantSurfaceType; id: string }; text: string }
  | { type: "question"; id: string }
  | { type: "note"; id: string }
  | { type: "coach_suggestion"; id: string }
  | { type: "ink_lasso"; surface: { type: string; id: string }; png: Blob; text: string }
  /** Segment 15, lasso → Ask on a PDF page: `id` is the ink surface `{upload_id}:{page}` (the server reads the
   * page text through S15's `pdf_page` resolver); `image` is the circled region as a PNG (page + ink). */
  | { type: "pdf_page"; id: string; image: Blob | null };

export interface AssistantAsk {
  kind: AssistantAskKind;
  context: AssistantAskContext;
  /** Prefilled input text for `kind: "ask"` (never sent without the student). */
  draft?: string;
}

export type AssistantAskHandler = (ask: AssistantAsk) => void;

const handlers: AssistantAskHandler[] = [];
const subscribers = new Set<() => void>();

function changed(): void {
  for (const s of subscribers) s();
}

/** Register the handler that answers asks; returns its unregister function. The latest registration answers. */
export function registerAssistantAsk(handler: AssistantAskHandler): () => void {
  handlers.push(handler);
  changed();
  return () => {
    const i = handlers.lastIndexOf(handler);
    if (i >= 0) {
      handlers.splice(i, 1);
      changed();
    }
  };
}

/** Send an ask to the registered handler. False when no handler is registered (nothing happened). */
export function askAssistant(ask: AssistantAsk): boolean {
  const handler = handlers.at(-1);
  if (!handler) return false;
  handler(ask);
  return true;
}

/** Whether an ask of this kind is sent straight away (true) or opens the panel for the student to type. */
export function sendsAtOnce(ask: AssistantAsk): boolean {
  return ask.kind !== "ask";
}

function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

const available = () => handlers.length > 0;

/** True while a handler is registered: render ask affordances only then. */
export function useAssistantAvailable(): boolean {
  return useSyncExternalStore(subscribe, available, () => false);
}
