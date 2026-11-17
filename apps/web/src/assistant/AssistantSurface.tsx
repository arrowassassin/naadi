import { cx } from "@naadi/ui";
import { History, MessageSquarePlus, PanelRightOpen, X } from "lucide-react";
import { type KeyboardEvent, type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CanvasItemView, CanvasSheet, CanvasTabs } from "../canvas/CanvasView";
import { registerBuiltInCanvasKinds } from "../canvas/kinds";
import { closeCanvas, focusCanvasItem, useCanvas } from "../canvas/registry";
import { useUiText } from "../copy/useUiText";
import { useInvalidateThreads } from "./api";
import { type AssistantAsk, registerAssistantAsk, sendsAtOnce } from "./ask";
import { attachmentFromContext } from "./attachments";
import { Composer } from "./Composer";
import { useCanvasKeepNotes } from "./canvasKeep";
import { type AssistantLayout, useAssistantLayout } from "./layout";
import { usePageContext } from "./pageContext";
import { TutorRuntimeProvider } from "./runtime";
import { closeAssistant, getAssistantUi, openAssistant, setAssistantUi, useAssistantUi } from "./store";
import { ThreadView } from "./Thread";
import { ThreadList } from "./ThreadList";
import { type Tutor, useTutor } from "./useTutor";

/*
 * The assistant's surface (loaded lazily by AssistantHost.tsx, so the shell bundle stays small).
 *
 * The assistant in the app shell (segment 14). Mounted only while the `assistant` feature is on and no
 * timed/exam session is active on the server (useActiveExam); while mounted it registers the ask handler, so
 * every page's "Ask" affordance exists exactly while the assistant can answer.
 *
 * Layouts (layout.ts): side panel (laptop, iPad landscape; Lesson board), bottom sheet (iPad portrait; Tablet
 * board), full-screen sheet (phone; Phone board). The Dock bar sits in the shell's bottom bar on tablet and up
 * (its height is part of --shell-bottom, so nothing hides under it); on phone the tab bar's centre button opens
 * the full-screen sheet.
 */

type T = ReturnType<typeof useUiText>;

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

function onEscape(close: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key !== "Escape" || isTypingTarget(e.target) || e.defaultPrevented) return;
    e.stopPropagation();
    close();
  };
}

const ROUND =
  "flex size-11 shrink-0 items-center justify-center rounded-pill text-text-2 hover:bg-surface-2 hover:text-text";

function Header({ t, tutor, onClose }: { t: T; tutor: Tutor; onClose: () => void }) {
  const ui = useAssistantUi();
  const page = usePageContext();
  const threads = ui.view === "threads";
  return (
    <header className="flex flex-col gap-1 border-line-soft border-b px-4 py-2 tablet:px-5">
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className="size-3 shrink-0 rounded-full bg-violet" />
        <h2 className="m-0 font-bold font-display text-[1.25rem] text-text">{t("agent.title")}</h2>
        <span className="flex-1" />
        <button
          type="button"
          aria-pressed={threads}
          aria-label={t("agent.threads.title")}
          onClick={() => setAssistantUi({ view: threads ? "chat" : "threads" })}
          className={cx(ROUND, threads && "bg-surface-2 text-text")}
        >
          <History aria-hidden="true" size={18} />
        </button>
        <button
          type="button"
          aria-label={t("agent.threads.new")}
          onClick={() => {
            closeCanvas();
            tutor.openThread(null);
            setAssistantUi({ view: "chat" });
          }}
          disabled={tutor.isRunning}
          className={ROUND}
        >
          <MessageSquarePlus aria-hidden="true" size={18} />
        </button>
        <button type="button" aria-label={t("agent.close")} onClick={onClose} className={ROUND}>
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      {/* The page the answer is about, on its own line: a long title wraps instead of being cut. */}
      {page?.title && !threads ? (
        <p className="m-0 self-start rounded-[14px] bg-surface-2 px-2.5 py-1 text-text-2 text-ui-sm" data-page-chip="">
          {t("agent.header.on", { title: page.title })}
        </p>
      ) : null}
    </header>
  );
}

function EmptyState({ t }: { t: T }) {
  return (
    <div className="flex flex-col gap-2 py-2" data-thread-empty="">
      <p className="m-0 font-display text-[1.375rem] text-text leading-tight">{t("agent.empty.title")}</p>
      <p className="m-0 text-text-2">{t("agent.empty.body")}</p>
    </div>
  );
}

/** Header, thread (or the thread list, or a canvas tab in the side panel) and composer. */
function Conversation({ tutor, layout, onClose }: { tutor: Tutor; layout: AssistantLayout; onClose: () => void }) {
  const t = useUiText();
  const ui = useAssistantUi();
  const canvas = useCanvas();
  const [pane, setPane] = useState<"chat" | "canvas">("chat");
  const inPanel = layout === "panel";
  const showCanvasTabs = inPanel && canvas.items.length > 0;
  const activeCanvas = canvas.items.find((i) => i.key === canvas.active) ?? null;

  // A canvas item opening in the side panel takes the panel (the chat stays one tab away).
  const lastActive = useRef(canvas.active);
  useEffect(() => {
    if (canvas.active && canvas.active !== lastActive.current && inPanel) setPane("canvas");
    if (!canvas.active) setPane("chat");
    lastActive.current = canvas.active;
  }, [canvas.active, inPanel]);

  const onAsk = useCallback(
    (text: string, kind: AssistantAsk["kind"]) => void tutor.send(text, { ask: kind }),
    [tutor],
  );
  const showingCanvas = showCanvasTabs && pane === "canvas" && activeCanvas;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Header t={t} tutor={tutor} onClose={onClose} />
      {showCanvasTabs ? (
        <div className="border-line-soft border-b">
          <CanvasTabs
            items={canvas.items}
            active={canvas.active}
            chat={{ selected: pane === "chat", onSelect: () => setPane("chat"), label: t("canvas.chat_tab") }}
            onSelect={(key) => {
              focusCanvasItem(key);
              setPane("canvas");
            }}
          />
        </div>
      ) : null}
      {showingCanvas ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4" data-canvas="panel">
          <CanvasItemView key={activeCanvas.key} item={activeCanvas} />
        </div>
      ) : ui.view === "threads" ? (
        <ThreadList
          currentThreadId={tutor.threadId}
          onOpen={(id) => {
            closeCanvas();
            tutor.openThread(id);
            setAssistantUi({ view: "chat" });
          }}
          onNew={() => {
            closeCanvas();
            tutor.openThread(null);
            setAssistantUi({ view: "chat" });
          }}
        />
      ) : (
        <ThreadView tutor={tutor} empty={<EmptyState t={t} />} onAsk={onAsk} />
      )}
      {showingCanvas || ui.view === "threads" ? null : (
        <div className="border-line-soft border-t p-3">
          <Composer tutor={tutor} />
        </div>
      )}
    </div>
  );
}

/** Publishes the side panel's width (CSSOM, allowed by the CSP) so the page makes room for it. */
function usePanelInset(open: boolean, ref: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (!open || !ref.current) return;
    const el = ref.current;
    const apply = () =>
      root.style.setProperty("--assistant-panel-w", `${Math.round(el.getBoundingClientRect().width)}px`);
    apply();
    root.setAttribute("data-assistant-panel", "");
    let frame = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(apply); // never resize what is observed inside the observer's callback
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      root.removeAttribute("data-assistant-panel");
      root.style.removeProperty("--assistant-panel-w");
    };
  }, [open, ref]);
}

export function SidePanel({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  const ref = useRef<HTMLElement>(null);
  usePanelInset(true, ref);
  return (
    <aside
      ref={ref}
      aria-label={t("agent.panel_label")}
      data-assistant="panel"
      onKeyDown={onEscape(closeAssistant)}
      className="fixed top-[calc(var(--shell-top,0px)+8px)] right-4 bottom-4 z-30 flex w-[clamp(340px,32vw,460px)] flex-col overflow-hidden rounded-tile border border-line bg-surface-1"
    >
      <Conversation tutor={tutor} layout="panel" onClose={closeAssistant} />
    </aside>
  );
}

export function BottomSheet({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  return (
    <aside
      aria-label={t("agent.panel_label")}
      data-assistant="sheet"
      onKeyDown={onEscape(closeAssistant)}
      className="mx-auto flex h-[min(62dvh,640px)] w-full max-w-[56rem] flex-col overflow-hidden rounded-sheet-sm border border-violet-line bg-violet-ground"
    >
      <span aria-hidden="true" className="mx-auto mt-2 h-[5px] w-12 shrink-0 rounded-[3px] bg-violet-line" />
      <Conversation tutor={tutor} layout="sheet" onClose={closeAssistant} />
    </aside>
  );
}

export function FullScreen({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const shell = document.querySelector<HTMLElement>("[data-shell]");
    const before = document.activeElement as HTMLElement | null;
    shell?.setAttribute("inert", "");
    if (!ref.current?.contains(document.activeElement)) ref.current?.focus();
    return () => {
      shell?.removeAttribute("inert");
      before?.focus?.();
    };
  }, []);
  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-label={t("agent.panel_label")}
      tabIndex={-1}
      data-assistant="fullscreen"
      onKeyDown={onEscape(closeAssistant)}
      className="fixed inset-0 z-40 flex flex-col bg-ground pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] outline-none"
    >
      <Conversation tutor={tutor} layout="fullscreen" onClose={closeAssistant} />
    </div>,
    document.body,
  );
}

/** The Dock bar (closed state, tablet and up): open button + the bar; sending opens the conversation. */
export function Dock({ tutor }: { tutor: Tutor }) {
  const t = useUiText();
  return (
    <div className="mx-auto flex w-full max-w-[48.75rem] items-end gap-2" data-assistant="dock">
      <button
        type="button"
        onClick={() => openAssistant({ focus: true })}
        aria-label={t("agent.open")}
        className="flex size-[60px] shrink-0 items-center justify-center rounded-pill border border-violet-line bg-violet-ground text-violet-text hover:border-violet"
      >
        <PanelRightOpen aria-hidden="true" size={20} />
      </button>
      <div className="min-w-0 flex-1">
        <Composer
          tutor={tutor}
          compact
          onBeforeSend={() => {
            if (!getAssistantUi().open) openAssistant({ focus: true });
          }}
        />
      </div>
    </div>
  );
}

export default function AssistantSurface({ dockSlot }: { dockSlot: HTMLElement | null }) {
  useCanvasKeepNotes();
  const tutor = useTutor({ examMode: false });
  const ui = useAssistantUi();
  const layout = useAssistantLayout();
  const canvas = useCanvas();
  const t = useUiText();
  const tutorRef = useRef(tutor);
  tutorRef.current = tutor;
  const invalidateThreads = useInvalidateThreads();

  // A finished turn may have created or renamed the thread's row: refresh Past chats.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !tutor.isRunning) invalidateThreads();
    wasRunning.current = tutor.isRunning;
  }, [tutor.isRunning, invalidateThreads]);

  useEffect(() => registerBuiltInCanvasKinds(), []);

  // The ask handler, registered while this surface lives (feature on, no exam).
  useEffect(
    () =>
      registerAssistantAsk((ask) => {
        if (sendsAtOnce(ask)) {
          setAssistantUi({ pendingAsk: ask, view: "chat" });
          openAssistant();
          return;
        }
        const ctx = ask.context;
        setAssistantUi((s) => ({
          context: ctx.type === "selection" || ctx.type === "ink_lasso" ? s.context : ctx,
          draft: ask.draft ?? s.draft,
        }));
        void attachmentFromContext(ctx).then((r) => {
          if (r && "attachment" in r)
            setAssistantUi((s) => ({
              attachments: [...s.attachments, r.attachment].slice(-4),
              context: s.context ?? ctx,
            }));
        });
        openAssistant({ focus: true });
      }),
    [],
  );

  // An ask that sends at once: a new conversation about that thing.
  useEffect(() => {
    const ask = ui.pendingAsk;
    if (!ask) return;
    setAssistantUi({ pendingAsk: null });
    void (async () => {
      const r = await attachmentFromContext(ask.context);
      setAssistantUi({
        context: ask.context,
        attachments: r && "attachment" in r ? [r.attachment] : [],
      });
      closeCanvas();
      await tutorRef.current.send(t(`agent.ask.${ask.kind}`), { ask: ask.kind, newThread: true });
    })();
  }, [ui.pendingAsk, t]);

  // A canvas item opening while the conversation is closed opens it (side panel layout).
  const lastCanvas = useRef(canvas.active);
  useEffect(() => {
    if (canvas.active && canvas.active !== lastCanvas.current && layout === "panel") openAssistant();
    lastCanvas.current = canvas.active;
  }, [canvas.active, layout]);

  const open = ui.open;
  const dock = !open || layout === "sheet" ? open ? <BottomSheet tutor={tutor} /> : <Dock tutor={tutor} /> : null;
  return (
    <TutorRuntimeProvider tutor={tutor}>
      {layout !== "fullscreen" && dockSlot && dock ? createPortal(dock, dockSlot) : null}
      {open && layout === "panel" ? <SidePanel tutor={tutor} /> : null}
      {open && layout === "fullscreen" ? <FullScreen tutor={tutor} /> : null}
      {layout !== "panel" ? <CanvasSheet /> : null}
    </TutorRuntimeProvider>
  );
}
