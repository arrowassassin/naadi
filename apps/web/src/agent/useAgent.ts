import { useStream } from "@langchain/langgraph-sdk/react";
import { useCallback, useMemo, useRef, useState } from "react";
import { accessToken, useAuth } from "../auth/session";
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
 * - `error`: the run failed or the runtime could not be reached (`error.status` + `error.requestId` let the
 *   surface explain it without showing raw response text).
 *
 * By default every `start` begins a new thread, so a surface shows only the latest run (the diagnostics panel).
 * With `keepThread`, `start` continues the open thread (the tutor: one thread per conversation), `openThread`
 * reopens a past one (its latest state is read from the checkpoint) and `continueRun` resumes a cancelled or
 * failed run from its last checkpoint (after a session refresh, for instance).
 *
 * Every request carries a current access token (read at call time, so a refreshed session is used at once).
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

/** A failed run: the HTTP status when the runtime answered (null for a network failure) and its request id. */
export interface AgentError {
  status: number | null;
  requestId: string | null;
}

interface CustomEvent {
  type?: string;
  block?: AgentBlock;
}

function isCustomEvent(v: unknown): v is CustomEvent {
  return typeof v === "object" && v !== null;
}

function statusOf(err: unknown): number | null {
  const s = typeof err === "object" && err !== null ? (err as { status?: unknown }).status : undefined;
  return typeof s === "number" ? s : null;
}

const STREAM_MODE = ["messages-tuple", "custom", "updates", "values"] as const;

export interface UseAgentOptions {
  assistantId: string;
  examMode?: boolean;
  /** Continue the open thread on `start` instead of beginning a new one. */
  keepThread?: boolean;
  /** Every custom event as received (the surface validates it), in addition to the generic block list. */
  onCustomEvent?: (data: unknown) => void;
}

type SubmitOptions = Record<string, unknown>;

export function useAgent<State extends Record<string, unknown>>({
  assistantId,
  examMode = false,
  keepThread = false,
  onCustomEvent,
}: UseAgentOptions) {
  const auth = useAuth();
  const online = useOnlineStatus();
  const token = auth.status === "signed-in" ? auth.session.access_token : null;
  const [blocks, setBlocks] = useState<ReceivedBlock[]>([]);
  const [budget, setBudget] = useState<BudgetNotice | null>(null);
  const [runError, setRunError] = useState<AgentError | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  // The request id of the runtime's latest answer (both our middleware and Aegra's echo X-Request-ID).
  const lastRequestId = useRef<string | null>(null);
  const customRef = useRef(onCustomEvent);
  customRef.current = onCustomEvent;

  const headers = useMemo(() => (token ? { authorization: `Bearer ${token}` } : undefined), [token]);
  const callerOptions = useMemo(
    () => ({
      fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
        // A current token per request: supabase-js refreshes it near expiry, and after an explicit refresh the
        // next call uses the new one without waiting for a re-render.
        const fresh = await accessToken();
        const headers = new Headers(init?.headers);
        if (fresh) headers.set("authorization", `Bearer ${fresh}`);
        const res = await fetch(input, { ...init, headers });
        lastRequestId.current = res.headers.get("x-request-id");
        return res;
      },
    }),
    [],
  );
  const fail = useCallback((err: unknown) => {
    setRunError({ status: statusOf(err), requestId: lastRequestId.current });
  }, []);

  const stream = useStream<State>({
    apiUrl: window.location.origin,
    apiKey: null,
    assistantId,
    defaultHeaders: headers,
    callerOptions,
    threadId,
    onThreadId: setThreadId,
    fetchStateHistory: false,
    onCustomEvent: (data: unknown) => {
      customRef.current?.(data);
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
    onError: fail,
  });

  // A failed submit (network, 401, 5xx) becomes the `error` state; it must not surface as an unhandled
  // promise rejection.
  const submitSafely = useCallback(
    async (...args: Parameters<typeof stream.submit>) => {
      try {
        await stream.submit(...args);
      } catch (err) {
        fail(err);
      }
    },
    [stream, fail],
  );

  const start = useCallback(
    async (input: Partial<State>, options: SubmitOptions = {}) => {
      if (examMode || !online) return;
      setBlocks([]);
      setBudget(null);
      setRunError(null);
      if (!keepThread) stream.switchThread(null); // a new thread per run: the surface shows only the latest run
      await submitSafely(
        input as Parameters<typeof stream.submit>[0],
        {
          ...options,
          streamMode: [...STREAM_MODE],
        } as Parameters<typeof stream.submit>[1],
      );
    },
    [examMode, keepThread, online, stream, submitSafely],
  );

  /** Reopen a past thread (null: a new, empty conversation). Its state is read from the latest checkpoint. */
  const openThread = useCallback(
    (id: string | null) => {
      setBlocks([]);
      setBudget(null);
      setRunError(null);
      stream.switchThread(id);
      setThreadId(id);
    },
    [stream],
  );

  /** Resume a cancelled or failed run of the open thread from its last checkpoint. */
  const continueRun = useCallback(async () => {
    if (examMode || !online || !threadId) return;
    setRunError(null);
    await submitSafely(null, { streamMode: [...STREAM_MODE] });
  }, [examMode, online, threadId, submitSafely]);

  const resume = useCallback(
    async (value: unknown) => {
      if (!online) return;
      setRunError(null);
      await submitSafely(null, { command: { resume: value }, streamMode: [...STREAM_MODE] });
    },
    [online, submitSafely],
  );

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
    error: runError ?? (stream.error ? { status: statusOf(stream.error), requestId: lastRequestId.current } : null),
    interrupt: stream.interrupt?.value as Record<string, unknown> | undefined,
    values: stream.values,
    messages: stream.messages,
    isLoading: stream.isLoading,
    threadId,
    start,
    resume,
    openThread,
    continueRun,
    stop: stream.stop,
  };
}
