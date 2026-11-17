import {
  type AppendMessage,
  AssistantRuntimeProvider,
  useExternalMessageConverter,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { convertLangChainMessages, type LangChainMessage } from "@assistant-ui/react-langgraph";
import type { ReactNode } from "react";
import type { Tutor } from "./useTutor";

function appendText(m: AppendMessage): string {
  return m.content
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("")
    .trim();
}

/**
 * assistant-ui on the tutor stream: an external-store runtime whose messages are `useStream`'s LangChain
 * messages converted by assistant-ui's LangGraph converter. The stream, the thread and every state stay in
 * `useAgent` (one way to talk to a graph); assistant-ui renders them (thread viewport, messages, parts).
 * No assistant-cloud: nothing here is configured with it.
 */
export function TutorRuntimeProvider({ tutor, children }: { tutor: Tutor; children: ReactNode }) {
  const messages = useExternalMessageConverter<LangChainMessage>({
    callback: convertLangChainMessages,
    messages: tutor.messages as unknown as LangChainMessage[],
    isRunning: tutor.isRunning,
  });
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: tutor.isRunning,
    isDisabled: tutor.phase === "exam_mode" || tutor.phase === "offline",
    onNew: async (m: AppendMessage) => {
      const text = appendText(m);
      if (text) await tutor.send(text);
    },
    onCancel: async () => tutor.stop(),
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
