import { cx } from "@naadi/ui";
import { X } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useId, useRef, useSyncExternalStore } from "react";
import { useUiText } from "../copy/useUiText";
import { type PageNotesHandle, PageNotesPanel } from "./PageNotesPanel";

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
        "mx-auto mt-auto mb-0 max-h-[88dvh] w-full max-w-none rounded-t-sheet-sm border-0 bg-paper p-0 text-ground",
        "tablet:w-[min(44rem,100%)] backdrop:bg-ground/80 open:animate-[naadi-sheet-in_var(--duration-sheet)_var(--ease-out)]",
      )}
    >
      {open ? (
        <div className="flex max-h-[inherit] flex-col">
          <div className="flex items-center justify-between px-5 pt-3">
            <span aria-hidden="true" className="mx-auto h-1.5 w-12 rounded-pill bg-ground/20" />
          </div>
          <span id={labelId} className="sr-only">
            {t("notes.panel_title")}
          </span>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-2 pb-[max(20px,env(safe-area-inset-bottom))]">
            <div className="flex justify-end">
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
  open,
  onClose,
  sectionId,
  sectionHeading,
  panelRef,
  onInkChange,
  article,
}: {
  open: boolean;
  onClose: () => void;
  sectionId: string;
  sectionHeading: string;
  panelRef: RefObject<PageNotesHandle | null>;
  onInkChange: (on: boolean) => void;
  article: ReactNode;
}) {
  const side = useSidePanel();
  const t = useUiText();
  const panel = (
    <PageNotesPanel
      key={sectionId}
      ref={panelRef}
      sectionId={sectionId}
      sectionHeading={sectionHeading}
      onInkChange={onInkChange}
    />
  );
  if (side) {
    return (
      <div className={cx("flex flex-col gap-5", open && "laptop:flex-row laptop:items-start")}>
        <div className="min-w-0 flex-1">{article}</div>
        {open ? (
          <aside
            aria-label={t("notes.panel_region")}
            // Below the shell's top bar and above the Reader's page bar (both publish their height).
            className="sticky top-[calc(var(--shell-top,0px)+16px)] flex max-h-[calc(100dvh-var(--shell-top,0px)-var(--reader-bar,0px)-var(--shell-bottom,0px)-32px)] w-[min(34rem,42%)] shrink-0 flex-col overflow-y-auto overscroll-contain rounded-tile bg-paper p-5 text-ground"
          >
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                aria-label={t("notes.panel_close")}
                className="-mb-2 flex size-11 items-center justify-center rounded-pill border border-ground/30 bg-transparent text-ground"
              >
                <X aria-hidden="true" size={18} />
              </button>
            </div>
            {panel}
          </aside>
        ) : null}
      </div>
    );
  }
  return (
    <>
      {article}
      <PaperSheet open={open} onClose={onClose}>
        {panel}
      </PaperSheet>
    </>
  );
}
