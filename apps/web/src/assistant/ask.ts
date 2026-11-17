import { useSyncExternalStore } from "react";

/**
 * How a page hands a question to the assistant (segment 14) without importing it. The assistant registers one
 * handler while its panel can answer; pages show their "ask" affordances (the review page's Stuck? panel) only
 * while a handler is registered, so a button never does nothing.
 *
 * Contract for segment 14: call `registerAssistantAsk(handler)` when the panel mounts and call the returned
 * function when it unmounts. `handler` opens the panel and answers `ask.kind` with `ask.context` as the page
 * context (for a flashcard: its id; read the card through the Cards API, which applies the attempt gate).
 * The assistant is disabled in exam mode: do not register there.
 */
export interface AssistantAsk {
  kind: "hint" | "explain";
  context:
    | { type: "flashcard"; id: string }
    /** Segment 15, lasso → Ask on a PDF page: `id` is the ink surface `{upload_id}:{page}` (read the page text
     * through GET /api/ingest/uploads/{id}/pages/{n}); `image` is the circled region as a PNG (page + ink). */
    | { type: "pdf_page"; id: string; image: Blob | null };
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

function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

const available = () => handlers.length > 0;

/** True while a handler is registered: render ask affordances only then. */
export function useAssistantAvailable(): boolean {
  return useSyncExternalStore(subscribe, available, () => false);
}
