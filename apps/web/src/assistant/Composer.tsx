import { AssistantBar, cx } from "@naadi/ui";
import { FileText, Image as ImageIcon, PenTool, Quote, WifiOff, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import type { AssistantAskContext } from "./ask";
import { type AttachError, addImageFiles, IMAGE_TYPES, MAX_ATTACHMENTS } from "./attachments";
import { useAssistantLayout } from "./layout";
import { usePageContext } from "./pageContext";
import { MAX_MESSAGE_CHARS } from "./protocol";
import { quickActionsFor } from "./quickActions";
import { type PendingAttachment, setAssistantUi, useAssistantUi } from "./store";
import type { Tutor } from "./useTutor";

type T = ReturnType<typeof useUiText>;

const CONTEXT_KEY: Record<AssistantAskContext["type"], string> = {
  flashcard: "agent.context.flashcard",
  section: "agent.context.section",
  selection: "agent.context.selection",
  question: "agent.context.question",
  note: "agent.context.note",
  coach_suggestion: "agent.context.coach",
  ink_lasso: "agent.context.ink",
  pdf_page: "agent.context.pdf_page",
};

const ATTACH_ERROR: Record<AttachError, string> = {
  type: "agent.attach.error_type",
  too_big: "agent.attach.error_size",
  too_many: "agent.attach.error_count",
  unreadable: "agent.attach.error_read",
};

const CHIP =
  "inline-flex min-h-11 max-w-full items-center gap-2 rounded-pill border border-line-strong bg-surface-2 pr-1 pl-3 text-text text-ui-sm";

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="flex size-11 shrink-0 items-center justify-center rounded-pill text-text-2 hover:text-text"
    >
      <X aria-hidden="true" size={16} />
    </button>
  );
}

function AttachmentChip({ a, t, onRemove }: { a: PendingAttachment; t: T; onRemove: () => void }) {
  if (a.kind === "selection") {
    return (
      <li className={CHIP}>
        <Quote aria-hidden="true" size={14} className="shrink-0 text-text-2" />
        <span className="min-w-0 max-w-[14rem] truncate">{a.text}</span>
        <RemoveButton label={t("agent.attach.remove_selection")} onClick={onRemove} />
      </li>
    );
  }
  return (
    <li className={CHIP}>
      <img src={a.dataUrl} alt="" className="size-7 shrink-0 rounded-[6px] bg-paper object-cover" />
      <span className="min-w-0 max-w-[10rem] truncate">{a.name || t("agent.attach.drawing")}</span>
      <RemoveButton
        label={t("agent.attach.remove", { name: a.name || t("agent.attach.drawing") })}
        onClick={onRemove}
      />
    </li>
  );
}

export function Composer({
  tutor,
  compact = false,
  onBeforeSend,
}: {
  tutor: Tutor;
  compact?: boolean;
  /** Called before every send (the Dock opens the conversation). */
  onBeforeSend?: () => void;
}) {
  const t = useUiText();
  const ui = useAssistantUi();
  const page = usePageContext();
  const layout = useAssistantLayout();
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [attachError, setAttachError] = useState<AttachError | null>(null);

  // An ask (or the shell's assistant button) asked for the input: focus it once the surface is on screen.
  const lastFocus = useRef(ui.focus);
  useEffect(() => {
    if (ui.focus === lastFocus.current) return;
    lastFocus.current = ui.focus;
    const id = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(id);
  }, [ui.focus]);

  const offline = tutor.phase === "offline";
  const running = tutor.isRunning;
  const waiting = tutor.confirm !== null;
  const chips = quickActionsFor(
    ui.context?.type === "flashcard" ? "flashcard" : ui.context?.type === "question" ? "question" : page?.type,
  );

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const r = await addImageFiles(ui.attachments, Array.from(files));
    setAssistantUi({ attachments: r.attachments });
    setAttachError(r.error);
    if (fileRef.current) fileRef.current.value = "";
  };

  const contextLabel = ui.context ? t(CONTEXT_KEY[ui.context.type]) : null;
  const before =
    ui.context || ui.attachments.length ? (
      <ul aria-label={t("agent.attach.list")} className="m-0 flex list-none flex-wrap gap-1.5 p-0 px-1 pt-1">
        {ui.context && ui.context.type !== "selection" && ui.context.type !== "ink_lasso" ? (
          <li className={cx(CHIP, "border-violet-line bg-violet-ground text-violet-text")}>
            <FileText aria-hidden="true" size={14} className="shrink-0" />
            <span className="min-w-0 truncate">{contextLabel}</span>
            <RemoveButton
              label={t("agent.context.remove", { what: contextLabel ?? "" })}
              onClick={() => setAssistantUi({ context: null })}
            />
          </li>
        ) : null}
        {ui.attachments.map((a) => (
          <AttachmentChip
            key={a.id}
            a={a}
            t={t}
            onRemove={() => setAssistantUi((s) => ({ attachments: s.attachments.filter((x) => x.id !== a.id) }))}
          />
        ))}
      </ul>
    ) : null;

  return (
    <div className="flex flex-col gap-2" data-composer="">
      {!compact && !offline ? (
        <ul aria-label={t("agent.chip.list")} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {chips.map((c) => (
            <li key={c.key} className="shrink-0">
              <button
                type="button"
                disabled={running || waiting}
                onClick={() => {
                  onBeforeSend?.();
                  void tutor.send(t(c.key), { ask: c.ask });
                }}
                className="min-h-11 whitespace-nowrap rounded-pill border border-line-strong px-3 text-text-2 text-ui-sm hover:border-text-3 hover:text-text disabled:cursor-not-allowed disabled:text-text-muted"
              >
                {t(c.key)}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {attachError ? (
        <p role="alert" className="m-0 flex items-center gap-2 text-amber text-ui-sm">
          <ImageIcon aria-hidden="true" size={14} />
          {t(ATTACH_ERROR[attachError], { max: String(MAX_ATTACHMENTS) })}
        </p>
      ) : null}
      {ui.context?.type === "ink_lasso" ? (
        <p className="m-0 flex items-center gap-2 text-text-2 text-ui-sm">
          <PenTool aria-hidden="true" size={14} />
          {t("agent.context.ink_hint")}
        </p>
      ) : null}
      <input
        ref={fileRef}
        type="file"
        accept={IMAGE_TYPES.join(",")}
        multiple
        hidden
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => void onFiles(e.target.files)}
      />
      <AssistantBar
        label={t("agent.bar.label")}
        inputLabel={t("agent.bar.input")}
        // The long placeholder (Dock board) only where it fits: the Dock and the tablet sheet; the side panel and
        // the phone sheet use the short one (Phone board).
        placeholder={t(
          page?.type === "section" && (compact || layout === "sheet")
            ? "agent.bar.placeholder_page"
            : "agent.bar.placeholder",
        )}
        attachLabel={t("agent.bar.attach")}
        sendLabel={t("agent.bar.send")}
        stopLabel={t("agent.bar.stop")}
        disabled={offline}
        disabledReason={offline ? t("agent.offline") : undefined}
        value={ui.draft}
        onValueChange={(draft) => setAssistantUi({ draft })}
        onSubmit={(text) => {
          onBeforeSend?.();
          return tutor.send(text);
        }}
        onAttach={ui.attachments.length < MAX_ATTACHMENTS ? () => fileRef.current?.click() : undefined}
        web={{ on: ui.web, onToggle: () => setAssistantUi((s) => ({ web: !s.web })), label: t("agent.bar.web") }}
        onStop={running ? () => tutor.stop() : undefined}
        sendBlocked={waiting}
        maxLength={MAX_MESSAGE_CHARS}
        inputRef={inputRef}
        before={before}
      />
      {waiting ? <p className="m-0 px-2 text-text-3 text-ui-sm">{t("agent.bar.confirm_first")}</p> : null}
      {offline ? (
        // The bar's placeholder truncates on a phone; the whole reason, with an icon (never colour alone).
        <p role="status" data-assistant-offline="" className="m-0 flex items-center gap-2 px-2 text-text-2 text-ui-sm">
          <WifiOff aria-hidden="true" size={16} className="shrink-0" />
          {t("agent.offline")}
        </p>
      ) : null}
    </div>
  );
}
