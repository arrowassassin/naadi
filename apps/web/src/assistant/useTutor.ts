import { useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type AgentError, useAgent } from "../agent/useAgent";
import { refreshAccessToken } from "../auth/session";
import { openInCanvas } from "../canvas/registry";
import type { AssistantAskContext, AssistantAskKind } from "./ask";
import { toAttachmentsIn } from "./attachments";
import { getPageContext, type PageContextRegistration, toPageContextIn } from "./pageContext";
import {
  type Block,
  type Citation,
  type ConfirmRequest,
  MAX_MESSAGE_CHARS,
  type Notice,
  type PageContextIn,
  parseCustomEvent,
  parseInterrupt,
  parseValues,
  type ResumeValue,
  type ToolEvent,
  type TutorInput,
} from "./protocol";
import { getAssistantUi, setAssistantUi } from "./store";

/**
 * The tutor conversation: `useAgent` (useStream against the Aegra runtime, graph "tutor", one thread per
 * conversation) plus everything the protocol adds on top. Custom events are validated (protocol.ts) and kept
 * for the live turn; the thread's `values` (read from the checkpoint when a thread is reopened) are the record.
 * Both are merged by id, so a block that streamed live and then arrived in `values` renders once.
 */
export type TutorPhase =
  | "idle"
  | "thinking" // a run started, no token yet
  | "streaming"
  | "interrupted"
  | "budget_exhausted"
  | "rate_limited"
  | "session_expired"
  | "error"
  | "offline"
  | "exam_mode";

type TutorState = Record<string, unknown> & { messages?: unknown[] };

/** The page context a send carries: an ask's own target wins over the page's registration. */
export function pageContextFor(
  ctx: AssistantAskContext | null,
  page: PageContextRegistration | null,
  route: string,
): PageContextIn {
  const base = toPageContextIn(page, route);
  if (!ctx) return base;
  switch (ctx.type) {
    case "flashcard":
      return { route, type: "flashcard", id: ctx.id };
    case "section":
      return { ...base, type: "section", id: ctx.id, chapter_id: ctx.chapter_id };
    case "question":
      return { route, type: "question", id: ctx.id, question: { id: ctx.id } };
    case "note":
      return { route, type: "note", id: ctx.id };
    case "coach_suggestion":
      return { route, type: "today", id: ctx.id };
    case "pdf_page":
      return { route, type: "pdf_page", id: ctx.id };
    default:
      return base;
  }
}

function byId<T extends { id: string }>(record: readonly T[], live: readonly T[]): T[] {
  const seen = new Set(record.map((x) => x.id));
  return [...record, ...live.filter((x) => !seen.has(x.id))];
}

function messageText(m: unknown): string {
  const c = (m as { content?: unknown }).content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c
      .map((p) =>
        typeof p === "object" && p && (p as { type?: unknown }).type === "text"
          ? String((p as { text?: unknown }).text ?? "")
          : "",
      )
      .join("");
  }
  return "";
}

export interface SendOptions {
  ask?: AssistantAskKind;
  /** Start a new conversation first (asks that send at once). */
  newThread?: boolean;
}

export function useTutor({ examMode }: { examMode: boolean }) {
  const route = useRouterState({ select: (s) => s.location.pathname });
  const [liveBlocks, setLiveBlocks] = useState<Block[]>([]);
  const [liveCitations, setLiveCitations] = useState<Citation[]>([]);
  const [tool, setTool] = useState<ToolEvent | null>(null);
  const [lastSent, setLastSent] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [started, setStarted] = useState(false);

  const onCustomEvent = useCallback((data: unknown) => {
    const ev = parseCustomEvent(data);
    if (!ev) return;
    if (ev.type === "block") {
      setLiveBlocks((prev) => [...prev.filter((b) => b.id !== ev.block.id), ev.block]);
      if (ev.block.type === "canvas") openInCanvas(ev.block.data);
    } else if (ev.type === "citation") {
      setLiveCitations((prev) => [...prev.filter((c) => c.id !== ev.citation.id), ev.citation]);
    } else {
      setTool(ev);
    }
  }, []);

  const agent = useAgent<TutorState>({ assistantId: "tutor", examMode, keepThread: true, onCustomEvent });
  const values = useMemo(() => parseValues(agent.values), [agent.values]);
  const blocks = useMemo(() => byId(values.blocks, liveBlocks), [values.blocks, liveBlocks]);
  const citations = useMemo(() => byId(values.citations, liveCitations), [values.citations, liveCitations]);
  const confirm: ConfirmRequest | null = useMemo(() => parseInterrupt(agent.interrupt), [agent.interrupt]);

  const messages = agent.messages;
  const lastAi = [...messages].reverse().find((m) => m.type === "ai");
  const hasToken = Boolean(lastAi && messageText(lastAi).length > 0 && messages.at(-1)?.type === "ai");

  // The run's notice (budget, rate limit, cap, unavailable): the turn's value, or the live notice block.
  const turnNotice: Notice | null = useMemo(() => {
    if (values.notice && values.notice.kind !== "disclaimer") return values.notice;
    const live = [...liveBlocks].reverse().find((b) => b.type === "notice" && b.data.kind !== "disclaimer");
    return live && live.type === "notice" ? live.data : null;
  }, [values.notice, liveBlocks]);

  const error: AgentError | null = agent.error;
  let phase: TutorPhase;
  if (examMode || values.refusal?.reason === "exam_mode") phase = "exam_mode";
  else if (agent.phase === "offline") phase = "offline";
  else if (agent.phase === "streaming") phase = started && !hasToken ? "thinking" : "streaming";
  else if (error?.status === 401) phase = "session_expired";
  else if (error?.status === 429) phase = "rate_limited";
  else if (agent.phase === "error") phase = "error";
  else if (turnNotice?.kind === "budget_exhausted") phase = "budget_exhausted";
  else if (turnNotice?.kind === "rate_limited") phase = "rate_limited";
  else if (confirm) phase = "interrupted";
  else phase = "idle";

  // A run that started streaming is no longer "starting".
  useEffect(() => {
    if (hasToken) setStarted(false);
  }, [hasToken]);
  useEffect(() => {
    if (!agent.isLoading) setStarted(false);
  }, [agent.isLoading]);

  // After an answer the tool status line goes.
  useEffect(() => {
    if (!agent.isLoading) setTool(null);
  }, [agent.isLoading]);

  const clearTurn = useCallback(() => {
    setLiveBlocks([]);
    setLiveCitations([]);
    setTool(null);
    setRefreshFailed(false);
  }, []);

  const send = useCallback(
    async (text: string, options: SendOptions = {}) => {
      const content = Array.from(text.trim()).slice(0, MAX_MESSAGE_CHARS).join("");
      if (!content) return;
      const ui = getAssistantUi();
      const input: TutorInput = {
        messages: [{ type: "human", content }],
        page_context: pageContextFor(ui.context, getPageContext(), route),
      };
      const attachments = toAttachmentsIn(ui.attachments);
      if (attachments.length) input.attachments = attachments;
      if (ui.web) input.web = true;
      if (options.ask) input.ask = { kind: options.ask };
      if (options.newThread) agent.openThread(null);
      clearTurn();
      setLastSent(content);
      setStarted(true);
      // The draft, attachments and chip are spent by this send; on a failure the draft comes back (below).
      setAssistantUi({ draft: "", attachments: [], context: null });
      await agent.start(input as unknown as Partial<TutorState>, {
        optimisticValues: (prev: TutorState) => ({
          ...prev,
          messages: [...((prev?.messages as unknown[]) ?? []), { type: "human", content, id: `pending-${Date.now()}` }],
        }),
      });
    },
    [agent, clearTurn, route],
  );

  // A failed send keeps what the student typed: put it back in an empty input.
  useEffect(() => {
    if (
      (phase === "error" || phase === "session_expired" || phase === "rate_limited") &&
      lastSent &&
      !getAssistantUi().draft
    ) {
      // A refused run (429) never started: the question shown is only the client's optimistic copy.
      const persisted =
        phase !== "rate_limited" && messages.some((m) => m.type === "human" && messageText(m) === lastSent);
      if (persisted) return;
      // After the bar has settled: it empties its text once the send resolves, which would wipe a draft put back
      // during the send (a refused run fails before the send resolves).
      const t = window.setTimeout(() => {
        if (!getAssistantUi().draft) setAssistantUi({ draft: lastSent });
      }, 50);
      return () => window.clearTimeout(t);
    }
  }, [phase, lastSent, messages]);

  const resume = useCallback(
    async (value: ResumeValue) => {
      clearTurn();
      setStarted(true);
      await agent.resume(value);
    },
    [agent, clearTurn],
  );

  /** Session expired mid-stream: refresh the session, then resume the same thread from its checkpoint. */
  const continueAfterRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshFailed(false);
    try {
      const fresh = await refreshAccessToken();
      if (!fresh) {
        setRefreshFailed(true);
        return;
      }
      const persisted = lastSent ? messages.some((m) => m.type === "human" && messageText(m) === lastSent) : true;
      if (agent.threadId && persisted) {
        clearTurn();
        setStarted(true);
        if (getAssistantUi().draft === lastSent) setAssistantUi({ draft: "" });
        await agent.continueRun();
      } else if (lastSent) {
        await send(getAssistantUi().draft || lastSent);
      }
    } finally {
      setRefreshing(false);
    }
  }, [agent, clearTurn, lastSent, messages, send]);

  /** Retry after an error: a run with a checkpoint resumes from it, otherwise the last message is sent again. */
  const retry = useCallback(async () => {
    const persisted = lastSent ? messages.some((m) => m.type === "human" && messageText(m) === lastSent) : true;
    if (agent.threadId && persisted) {
      clearTurn();
      setStarted(true);
      await agent.continueRun();
    } else if (lastSent) {
      await send(getAssistantUi().draft || lastSent);
    }
  }, [agent, clearTurn, lastSent, messages, send]);

  const openThread = useCallback(
    (id: string | null) => {
      clearTurn();
      setLastSent(null);
      agent.openThread(id);
    },
    [agent, clearTurn],
  );

  // Canvas items of a reopened thread come back from the checkpoint; the chips reopen them on demand.
  const canvasRefs = values.canvas;

  const stoppedRef = useRef(false);
  const stop = useCallback(() => {
    stoppedRef.current = true;
    agent.stop();
  }, [agent]);

  return {
    phase,
    messages,
    isRunning: agent.isLoading,
    threadId: agent.threadId,
    blocks,
    citations,
    canvasRefs,
    confirm,
    tool,
    turnNotice,
    error,
    refreshing,
    refreshFailed,
    disclaimerShown: values.disclaimer_shown,
    send,
    resume,
    retry,
    continueAfterRefresh,
    openThread,
    stop,
  };
}

export type Tutor = ReturnType<typeof useTutor>;
