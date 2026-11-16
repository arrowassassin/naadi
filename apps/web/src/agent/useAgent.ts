import { useStream } from "@langchain/langgraph-sdk/react";
import { useCallback, useMemo, useState } from "react";
import { useAuth } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";

/**
 * The one way the web app talks to an agent graph: `useStream` from the official LangGraph SDK against the
 * Aegra runtime on the same origin (Agent Protocol routes at the root: /threads, /runs, ...), sending the
 * Supabase access token. On top of the stream it derives the states every agent surface shows:
 *
 * - `exam_mode`: the assistant is silent in practice-exam mode (nothing is submitted);
 * - `offline`: the device has no connection (nothing is submitted);
 * - `streaming` → `interrupted` (a write waits for the student's confirm) → `idle`;
 * - `budget_exhausted`: a graph reported that the AI quota is spent (custom `notice` block with the reset
 *   time); the surface shows when it resets;
 * - `error`: the run failed or the runtime could not be reached.
 */
export type AgentPhase = "idle" | "streaming" | "interrupted" | "budget_exhausted" | "error" | "offline" | "exam_mode";

export interface AgentBlock {
  type: string;
  data: Record<string, unknown>;
}

/** A block as received, with a stable id for rendering (blocks arrive in order and are never reordered). */
export interface ReceivedBlock extends AgentBlock {
  id: number;
}

export interface BudgetNotice {
  resetAt: Date;
}

interface CustomEvent {
  type?: string;
  block?: AgentBlock;
}

function isCustomEvent(v: unknown): v is CustomEvent {
  return typeof v === "object" && v !== null;
}

export interface UseAgentOptions {
  assistantId: string;
  examMode?: boolean;
}

export function useAgent<State extends Record<string, unknown>>({ assistantId, examMode = false }: UseAgentOptions) {
  const auth = useAuth();
  const online = useOnlineStatus();
  const token = auth.status === "signed-in" ? auth.session.access_token : null;
  const [blocks, setBlocks] = useState<ReceivedBlock[]>([]);
  const [budget, setBudget] = useState<BudgetNotice | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);

  const headers = useMemo(() => (token ? { authorization: `Bearer ${token}` } : undefined), [token]);
  const stream = useStream<State>({
    apiUrl: window.location.origin,
    assistantId,
    defaultHeaders: headers,
    threadId,
    onThreadId: setThreadId,
    fetchStateHistory: false,
    onCustomEvent: (data: unknown) => {
      if (!isCustomEvent(data) || data.type !== "block" || !data.block) return;
      const block = data.block;
      if (
        block.type === "notice" &&
        block.data.kind === "budget_exhausted" &&
        typeof block.data.reset_at === "string"
      ) {
        setBudget({ resetAt: new Date(block.data.reset_at) });
        return;
      }
      setBlocks((prev) => [...prev, { ...block, id: prev.length + 1 }]);
    },
    onError: (err: unknown) => setRunError(err instanceof Error ? err.message : "The agent run failed."),
  });

  // A failed submit (network, 401, 5xx) becomes the `error` state; it must not surface as an unhandled
  // promise rejection.
  const submitSafely = useCallback(
    async (...args: Parameters<typeof stream.submit>) => {
      try {
        await stream.submit(...args);
      } catch (err) {
        setRunError(err instanceof Error ? err.message : "The agent run failed.");
      }
    },
    [stream],
  );

  const start = useCallback(
    async (input: Partial<State>) => {
      if (examMode || !online) return;
      setBlocks([]);
      setBudget(null);
      setRunError(null);
      await submitSafely(input as Parameters<typeof stream.submit>[0], {
        streamMode: ["messages-tuple", "custom", "updates", "values"],
      });
    },
    [examMode, online, submitSafely],
  );

  const resume = useCallback(
    async (value: unknown) => {
      if (!online) return;
      setRunError(null);
      await submitSafely(null, {
        command: { resume: value },
        streamMode: ["messages-tuple", "custom", "updates", "values"],
      });
    },
    [online, submitSafely],
  );

  const reset = useCallback(() => {
    setThreadId(null);
    setBlocks([]);
    setBudget(null);
    setRunError(null);
  }, []);

  let phase: AgentPhase;
  if (examMode) phase = "exam_mode";
  else if (!online) phase = "offline";
  else if (stream.isLoading) phase = "streaming";
  else if (runError || stream.error) phase = "error";
  else if (budget) phase = "budget_exhausted";
  else if (stream.interrupt) phase = "interrupted";
  else phase = "idle";

  const text = stream.messages
    .filter((m) => m.type === "ai")
    .map((m) => (typeof m.content === "string" ? m.content : ""))
    .join("");

  return {
    phase,
    text,
    blocks,
    budget,
    error:
      runError ??
      (stream.error instanceof Error ? stream.error.message : stream.error ? "The agent run failed." : null),
    interrupt: stream.interrupt?.value as Record<string, unknown> | undefined,
    values: stream.values,
    threadId,
    start,
    resume,
    stop: stream.stop,
    reset,
  };
}
