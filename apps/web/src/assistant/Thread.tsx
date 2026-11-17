import { MessagePrimitive, ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import { Button, ErrorState } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { LogIn, RotateCw, Sparkles } from "lucide-react";
import { createContext, type ReactNode, useContext, useMemo } from "react";
import { useUiText } from "../copy/useUiText";
import { type ActionHandlers, BlockView, NoticeBlock, SourcesBlock } from "./blocks";
import { ConfirmChip } from "./ConfirmChip";
import { Markdown } from "./Markdown";
import { type Block, type Citation, TOOL_NAMES } from "./protocol";
import type { Tutor } from "./useTutor";

/*
 * The conversation: assistant-ui's thread viewport and message primitives over the tutor runtime. The student's
 * messages are paper-coloured bubbles; everything the assistant wrote sits on violet framing. Each AI message
 * renders its markdown (Streamdown), then its blocks (by message_id), then its sources. Blocks that belong to no
 * rendered message (thread level, or a message the stream has not named yet) follow the last message.
 */

interface ThreadData {
  tutor: Tutor;
  blocksFor: (id: string) => Block[];
  citationsFor: (id: string) => Citation[];
  disclaimerId: string | null;
  handlers: ActionHandlers;
}

const Ctx = createContext<ThreadData | null>(null);

function useThreadData(): ThreadData {
  const v = useContext(Ctx);
  if (!v) throw new Error("ThreadView context missing");
  return v;
}

function UserText({ text }: { text: string }) {
  return <p className="m-0 whitespace-pre-wrap break-words">{text}</p>;
}

function UserMessage() {
  const t = useUiText();
  return (
    <MessagePrimitive.Root className="flex flex-col items-end" data-message-role="user">
      <span className="sr-only">{t("agent.thread.you")}</span>
      <div className="max-w-[88%] rounded-[20px_20px_6px_20px] bg-control px-4 py-3 text-text text-ui">
        <MessagePrimitive.Parts components={{ Text: UserText }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantText({ text }: { text: string }) {
  const { tutor, citationsFor } = useThreadData();
  const id = useAuiState((s) => s.message.id);
  const running = useAuiState((s) => s.message.status?.type === "running");
  const citations = useMemo(() => citationsFor(id), [citationsFor, id]);
  return <Markdown text={text} streaming={running && tutor.isRunning} citations={citations} />;
}

const noTool = () => null;

function AssistantMessage() {
  const t = useUiText();
  const { tutor, blocksFor, citationsFor, disclaimerId, handlers } = useThreadData();
  const id = useAuiState((s) => s.message.id);
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text.trim().length > 0));
  const blocks = blocksFor(id);
  if (!hasText && blocks.length === 0) return null;
  const hasSources = blocks.some((b) => b.type === "sources");
  const citations = hasSources ? [] : citationsFor(id);
  return (
    <MessagePrimitive.Root className="flex flex-col gap-3" data-message-role="assistant" data-message-id={id}>
      <span className="sr-only">{t("agent.thread.naadi")}</span>
      {blocks
        .filter((b) => b.type === "notice" && b.data.kind === "disclaimer" && b.id === disclaimerId)
        .map((b) => (
          <BlockView key={b.id} block={b} handlers={handlers} busy={tutor.isRunning} />
        ))}
      <MessagePrimitive.Parts components={{ Text: AssistantText, tools: { Fallback: noTool } }} />
      {blocks
        .filter((b) => !(b.type === "notice" && b.data.kind === "disclaimer"))
        .map((b) => (
          <BlockView key={b.id} block={b} handlers={handlers} busy={tutor.isRunning} />
        ))}
      {citations.length ? <SourcesBlock items={citations} /> : null}
    </MessagePrimitive.Root>
  );
}

function ToolStatus({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  if (tutor.phase === "thinking" && !(tutor.tool && tutor.tool.status === "running")) {
    return (
      <p role="status" className="m-0 flex items-center gap-2 text-text-2 text-ui-sm" data-status="thinking">
        <Sparkles aria-hidden="true" size={16} className="animate-pulse text-violet" />
        {t("agent.thinking")}
      </p>
    );
  }
  if (!tutor.isRunning || !tutor.tool || tutor.tool.status !== "running") return null;
  const known = (TOOL_NAMES as readonly string[]).includes(tutor.tool.tool);
  return (
    <p
      role="status"
      className="m-0 flex items-center gap-2 text-text-2 text-ui-sm"
      data-status="tool"
      data-tool={tutor.tool.tool}
    >
      <Sparkles aria-hidden="true" size={16} className="animate-pulse text-violet" />
      {t(known ? `agent.tool.${tutor.tool.tool}` : "agent.tool.working")}
    </p>
  );
}

function RunState({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  if (tutor.phase === "session_expired") {
    return (
      <div
        role="alert"
        className="flex flex-col gap-3 rounded-block border border-amber/60 bg-surface-2 p-4"
        data-state="session_expired"
      >
        <p className="m-0 font-semibold text-text">{t("agent.session.title")}</p>
        <p className="m-0 text-text-2">{t(tutor.refreshFailed ? "agent.session.failed" : "agent.session.body")}</p>
        {tutor.refreshFailed ? (
          <Link to="/sign-in" className="inline-flex min-h-11 items-center gap-2 self-start text-violet-text">
            <LogIn aria-hidden="true" size={16} /> {t("agent.session.sign_in")}
          </Link>
        ) : (
          <Button
            variant="assistant"
            className="self-start"
            icon={<RotateCw size={16} className={tutor.refreshing ? "animate-spin" : undefined} />}
            disabled={tutor.refreshing}
            onClick={() => void tutor.continueAfterRefresh()}
          >
            {t("agent.session.continue")}
          </Button>
        )}
      </div>
    );
  }
  if (tutor.phase === "rate_limited" && !tutor.turnNotice) {
    return <NoticeBlock notice={{ kind: "unavailable" }} />;
  }
  if (tutor.phase === "error" && tutor.error) {
    const status = tutor.error.status;
    const message =
      status === null
        ? t("agent.error.network")
        : status === 409
          ? t("agent.exam_mode")
          : status >= 500
            ? t("agent.error.server")
            : t("agent.error.generic");
    return (
      <ErrorState
        title={t("agent.error.title")}
        message={message}
        requestId={tutor.error.requestId}
        onRetry={status === 409 ? undefined : () => void tutor.retry()}
        retryLabel={t("agent.error.retry")}
      />
    );
  }
  return null;
}

export function ThreadView({ tutor, empty, onAsk }: { tutor: Tutor; empty: ReactNode; onAsk: ActionHandlers["ask"] }) {
  const t = useUiText();
  const aiIds = useMemo(
    () => new Set(tutor.messages.filter((m) => m.type === "ai" && typeof m.id === "string").map((m) => m.id as string)),
    [tutor.messages],
  );
  const data = useMemo<ThreadData>(() => {
    const byMessage = new Map<string, Block[]>();
    const orphans: Block[] = [];
    for (const b of tutor.blocks) {
      if (b.message_id && aiIds.has(b.message_id))
        byMessage.set(b.message_id, [...(byMessage.get(b.message_id) ?? []), b]);
      else orphans.push(b);
    }
    const cites = new Map<string, Citation[]>();
    const looseCites: Citation[] = [];
    for (const c of tutor.citations) {
      if (c.message_id && aiIds.has(c.message_id)) cites.set(c.message_id, [...(cites.get(c.message_id) ?? []), c]);
      else looseCites.push(c);
    }
    const last = [...aiIds].at(-1) ?? null;
    const firstDisclaimer = tutor.blocks.find((b) => b.type === "notice" && b.data.kind === "disclaimer");
    return {
      tutor,
      blocksFor: (id) => byMessage.get(id) ?? [],
      // Citations the stream has not tied to a rendered message yet belong to the latest answer.
      citationsFor: (id) => [...(cites.get(id) ?? []), ...(id === last ? looseCites : [])],
      disclaimerId: firstDisclaimer?.id ?? null,
      handlers: { ask: onAsk },
      orphans,
    } as ThreadData & { orphans: Block[] };
  }, [tutor, aiIds, onAsk]);
  const orphans = (data as ThreadData & { orphans: Block[] }).orphans;
  const lastAiId = [...aiIds].at(-1) ?? null;

  return (
    <Ctx.Provider value={data}>
      <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
        <ThreadPrimitive.Viewport
          aria-label={t("agent.thread.label")}
          className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto overscroll-contain px-4 py-4 tablet:px-6"
          data-thread=""
        >
          <ThreadPrimitive.Empty>{empty}</ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages>
            {({ message }) => (message.role === "user" ? <UserMessage /> : <AssistantMessage />)}
          </ThreadPrimitive.Messages>
          {orphans.length ? (
            <div className="flex flex-col gap-3" data-thread-blocks="" data-after={lastAiId ?? undefined}>
              {orphans
                .filter((b) => !(b.type === "notice" && b.data.kind === "disclaimer" && b.id !== data.disclaimerId))
                .map((b) => (
                  <BlockView key={b.id} block={b} handlers={data.handlers} busy={tutor.isRunning} />
                ))}
            </div>
          ) : null}
          {tutor.confirm ? (
            <ConfirmChip
              key={JSON.stringify(tutor.confirm)}
              request={tutor.confirm}
              busy={tutor.isRunning}
              onResume={(v) => void tutor.resume(v)}
            />
          ) : null}
          <ToolStatus tutor={tutor} />
          <RunState tutor={tutor} />
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </Ctx.Provider>
  );
}
