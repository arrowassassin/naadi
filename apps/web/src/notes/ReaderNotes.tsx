import { cx } from "@naadi/ui";
import { MessageCircle, X } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useId, useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { useAssistantAvailable } from "../assistant/ask";
import { openAssistant } from "../assistant/store";
import { useUiText } from "../copy/useUiText";
import { KeepDockVisible } from "../shell/chrome";
import { lazyWithFallback } from "../shell/lazyPage";
import type { PageNotesHandle } from "./PageNotesPanel";
import { PAPER_NOTICE } from "./paperNotice";
import { prefetchChunks } from "./prefetch";

/** The panel (the notes API, the offline queue, the ink page) loads when it is first opened, never with the page. */
const loadPanel = () => import("./PageNotesPanel").then((m) => m.PageNotesPanel);
const loadEditor = () => import("./NoteEditor");
const PageNotesPanel = lazyWithFallback(loadPanel, { fallback: null, unavailable: "inline" });

/** Laptop and iPad landscape (≥ 1100 px, the `laptop` breakpoint): the panel sits beside the page. */
const SIDE_QUERY = "(min-width: 68.75rem)";

function subscribeSide(cb: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
  const mq = window.matchMedia(SIDE_QUERY);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}

export function useSidePanel(): boolean {
  return useSyncExternalStore(
    subscribeSide,
    () => typeof window.matchMedia === "function" && window.matchMedia(SIDE_QUERY).matches,
    () => false,
  );
}

const OPEN_KEY = "naadi.page-notes.open.v1";

export function readNotesOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeNotesOpen(open: boolean): void {
  try {
    if (open) localStorage.setItem(OPEN_KEY, "1");
    else localStorage.removeItem(OPEN_KEY);
  } catch {
    // private mode
  }
}

/** A bottom sheet on paper (native <dialog>: focus trap, Esc, inert page behind) for phone and iPad portrait. */
function PaperSheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const t = useUiText();
  const labelId = useId();
  const chatAvailable = useAssistantAvailable();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) d.close();
  }, [open]);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: keyboard users close with Esc or the Close button
    <dialog
      ref={ref}
      aria-labelledby={labelId}
      data-page-notes-sheet=""
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className={cx(
        "mx-auto mt-auto mb-0 h-[calc(100dvh-env(safe-area-inset-top)-0.5rem)] max-h-none w-full max-w-none rounded-t-sheet-sm border-0 bg-paper p-0 text-ground",
        "tablet:w-[min(44rem,100%)] backdrop:bg-ground/80 open:animate-[naadi-sheet-in_var(--duration-sheet)_var(--ease-out)]",
      )}
    >
      {open ? (
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between px-5 pt-3">
            <span aria-hidden="true" className="mx-auto h-1.5 w-12 rounded-pill bg-ground/20" />
          </div>
          <span id={labelId} className="sr-only">
            {t("notes.panel_title")}
          </span>
          <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-5 pt-2 pb-[max(20px,env(safe-area-inset-bottom))]">
            <div className="absolute top-2 right-2 z-10 flex">
              {chatAvailable && (
                <button
                  type="button"
                  aria-label={t("agent.open")}
                  className="flex size-11 items-center justify-center rounded-pill text-ground"
                  onClick={() => {
                    onClose();
                    openAssistant({ focus: true });
                  }}
                >
                  <MessageCircle aria-hidden="true" size={18} />
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                aria-label={t("notes.panel_close")}
                className="flex size-11 items-center justify-center rounded-pill border border-ground/30 bg-transparent text-ground"
              >
                <X aria-hidden="true" size={18} />
              </button>
            </div>
            {children}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}

/**
 * Where the page-notes panel goes: beside the page on laptop / iPad landscape (a sticky paper column), a bottom
 * sheet on phone and iPad portrait. `article` is the reading page.
 */
export function ReaderNotesLayout({
  side,
  open,
  onClose,
  sectionId,
  sectionHeading,
  panelRef,
  onInkChange,
  onPanelMounted,
  article,
}: {
  side: boolean;
  open: boolean;
  onClose: () => void;
  sectionId: string;
  sectionHeading: string;
  panelRef: RefObject<PageNotesHandle | null>;
  onInkChange: (on: boolean) => void;
  /** The panel has mounted (it is a lazy chunk): `panelRef` is set, so anything queued for it can be sent. */
  onPanelMounted?: () => void;
  article: ReactNode;
}) {
  const lesson = useRef<HTMLDivElement>(null);
  const notesPane = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const column = lesson.current;
    const pane = notesPane.current;
    if (!side || !open || !column || !pane) return;
    const root = document.documentElement;
    const measure = () => {
      const rect = column.getBoundingClientRect();
      root.style.setProperty("--lesson-left", rect.left + "px");
      root.style.setProperty("--lesson-width", rect.width + "px");
    };
    // The chapter header can put the pane below its sticky inset until the lesson scrolls.
    const fitPaper = () => {
      const available = window.innerHeight - pane.getBoundingClientRect().top - 16;
      pane.style.maxHeight = Math.max(0, available) + "px";
    };
    measure();
    fitPaper();
    const observer = new ResizeObserver(() => {
      measure();
      fitPaper();
    });
    observer.observe(column);
    window.addEventListener("resize", measure);
    window.addEventListener("resize", fitPaper);
    window.addEventListener("scroll", fitPaper, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("resize", fitPaper);
      window.removeEventListener("scroll", fitPaper);
      root.style.removeProperty("--lesson-left");
      root.style.removeProperty("--lesson-width");
    };
  }, [side, open]);
  const t = useUiText();
  // The panel and its editor are fetched once the page has settled, so they are on the device before the student
  // can go offline.
  useEffect(() => prefetchChunks(loadPanel, loadEditor), []);
  const panel = (
    <div className={cx("flex flex-1 flex-col", PAPER_NOTICE)}>
      <PageNotesPanel
        key={sectionId}
        ref={panelRef}
        sectionId={sectionId}
        sectionHeading={sectionHeading}
        onInkChange={onInkChange}
        onMounted={onPanelMounted}
      />
    </div>
  );
  // The article keeps ONE position in the tree whatever the viewport: moving it between a wrapper and a fragment when
  // the side-panel breakpoint is crossed (an iPad turned, a window resized) would remount it and reset everything
  // stateful inside the page, such as a playing video.
  return (
    <KeepDockVisible.Provider value={side && open}>
      <div className={cx("flex flex-col gap-5", side && open && "laptop:flex-row laptop:items-start")}>
        <div ref={lesson} className="min-w-0 flex-1">
          {article}
        </div>
        {side && open ? (
          <aside
            ref={notesPane}
            data-reader-notes-side=""
            aria-label={t("notes.panel_region")}
            // The bottom dock is confined to the adjacent lesson column.
            className="sticky top-[calc(var(--shell-top,0px)+16px)] flex h-[calc(100dvh-var(--shell-top,0px)-32px)] w-[42%] shrink-0 flex-col overflow-y-auto overscroll-contain rounded-tile bg-paper p-4 text-ground"
          >
            <div className="absolute top-3 right-4 z-10">
              <button
                type="button"
                onClick={onClose}
                aria-label={t("notes.panel_close")}
                className="flex size-11 items-center justify-center rounded-pill border border-ground/30 bg-transparent text-ground"
              >
                <X aria-hidden="true" size={18} />
              </button>
            </div>
            {panel}
          </aside>
        ) : null}
      </div>
      {side ? null : (
        <PaperSheet open={open} onClose={onClose}>
          {panel}
        </PaperSheet>
      )}
    </KeepDockVisible.Provider>
  );
}
