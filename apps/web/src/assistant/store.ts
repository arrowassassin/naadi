import { useSyncExternalStore } from "react";
import type { AssistantAsk, AssistantAskContext } from "./ask";

/*
 * The assistant's UI state, outside React so it survives the surface switching layout (panel ↔ sheet ↔ full
 * screen) and a session refresh mid-stream: open/closed, the view, the draft, pending attachments, the context
 * chip an "ask" attached, the web toggle, and an ask waiting to be sent once the tutor is ready.
 */

export type PendingAttachment =
  | {
      id: string;
      kind: "image";
      dataUrl: string;
      name: string;
      text: string | null;
      surface: { type: string; id: string } | null;
    }
  | { id: string; kind: "selection"; text: string; surface: { type: "section" | "note" | "question"; id: string } };

export interface AssistantUiState {
  open: boolean;
  expanded: boolean;
  view: "chat" | "threads";
  draft: string;
  attachments: PendingAttachment[];
  /** The context an ask attached (a chip above the input), sent with the next message. */
  context: AssistantAskContext | null;
  web: boolean;
  /** Bumped to move focus to the input. */
  focus: number;
  /** An ask that sends at once, consumed by the tutor. */
  pendingAsk: AssistantAsk | null;
}

const initial: AssistantUiState = {
  open: false,
  expanded: false,
  view: "chat",
  draft: "",
  attachments: [],
  context: null,
  web: false,
  focus: 0,
  pendingAsk: null,
};

let state: AssistantUiState = initial;
const listeners = new Set<() => void>();

export function getAssistantUi(): AssistantUiState {
  return state;
}

export function setAssistantUi(
  next: Partial<AssistantUiState> | ((s: AssistantUiState) => Partial<AssistantUiState>),
): void {
  const patch = typeof next === "function" ? next(state) : next;
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useAssistantUi(): AssistantUiState {
  return useSyncExternalStore(subscribe, getAssistantUi, getAssistantUi);
}

export function openAssistant(options: { focus?: boolean } = {}): void {
  setAssistantUi((s) => ({ open: true, view: "chat", focus: options.focus ? s.focus + 1 : s.focus }));
}

export function closeAssistant(): void {
  setAssistantUi({ open: false, expanded: false });
}

let attachmentCounter = 0;
export function nextAttachmentId(): string {
  attachmentCounter += 1;
  return `att-${attachmentCounter}`;
}

/** Tests only. */
export function resetAssistantUiForTests(): void {
  state = initial;
  for (const l of listeners) l();
}
