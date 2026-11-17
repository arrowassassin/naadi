import type { InkSelectionPayload } from "@naadi/ink";
import { Button, cx } from "@naadi/ui";
import { PenLine } from "lucide-react";
import { lazy, type PointerEvent, type ReactNode, Suspense, useCallback, useEffect, useState } from "react";
import { useUiText } from "../../copy/useUiText";

/** Blocks with their own fills (tables, figures, callouts, embeds): markers multiply over them (markerModeFor). */
const OPAQUE_BLOCKS = new Set(["table", "figure", "video", "pdf", "questionEmbed", "mermaid", "math", "callout"]);

export function hasOpaqueBlocks(doc: unknown): boolean {
  const content = (doc as { content?: unknown } | null)?.content;
  return Array.isArray(content) && content.some((b) => OPAQUE_BLOCKS.has((b as { type?: string })?.type ?? ""));
}

export interface SectionInkProps {
  sectionId: string;
  /** The section body (TipTap JSON): re-measure block anchors when it changes. */
  doc: unknown;
  /** Ink on (the surface takes Pencil input and the toolbar replaces the page bar). */
  on: boolean;
  onToggle: (on: boolean) => void;
  /** The reader is showing its device copy (the network failed): the ink is read from the device too. */
  offline?: boolean;
  /** The content (ContentRenderer, whose top-level blocks carry data-block-pm). */
  children: ReactNode;
  /** Ask the assistant about a lasso selection (S14); the "Ask Naadi" action shows only when given. */
  onAsk?: (payload: InkSelectionPayload) => void;
  className?: string;
}

/** The engine, sync, Dexie and toolbar: a separate chunk, loaded after the section text has painted. */
const loadLive = () => import("./SectionInkLive");
const SectionInkLive = lazy(() => loadLive().then((m) => ({ default: m.SectionInkLive })));

/** Slots the live part fills (portals), so the page's layout and the content's elements never change. */
export interface SectionInkSlots {
  status: HTMLElement;
  notice: HTMLElement;
  after: HTMLElement;
  host: HTMLElement;
}

/**
 * Load the engine once the browser is idle after the first paint (Safari has no requestIdleCallback: a short
 * timeout after the load event instead), or at once when `now` turns true (the student asked for ink).
 */
function useDeferredLoad(now: boolean): boolean {
  const [load, setLoad] = useState(now);
  useEffect(() => {
    if (now) setLoad(true);
  }, [now]);
  useEffect(() => {
    if (load) return;
    let cancel: () => void = () => undefined;
    const go = () => setLoad(true);
    const idle = () => {
      if (typeof window.requestIdleCallback === "function") {
        const id = window.requestIdleCallback(go, { timeout: 3000 });
        cancel = () => window.cancelIdleCallback(id);
      } else {
        const id = window.setTimeout(go, 500);
        cancel = () => window.clearTimeout(id);
      }
    };
    // After the first paint: two animation frames, then the load event (all of the page's own requests done).
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (document.readyState === "complete") idle();
        else window.addEventListener("load", idle, { once: true });
      });
    });
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("load", idle);
      cancel();
    };
  }, [load]);
  return load;
}

/**
 * Ink on a Reader section (segment 12b): persisted and synced per section (`section` surface), anchored to the
 * paragraph under each stroke so it survives reflow between iPad and phone, with the save state as icon + word and
 * a notice when the section text changed after the ink was drawn (the ink is never dropped).
 *
 * Performance: this wrapper renders the section text, the Write button and an empty ink host at once and imports
 * nothing of the ink engine, its canvas or IndexedDB. Those load after the text has painted (when the browser is
 * idle), or immediately when the student turns ink on or touches the page with the pen. The live part attaches to
 * the same host (InkSurface `host`), so the content is never remounted; strokes appear as soon as it lands.
 */
export function SectionInk(props: SectionInkProps) {
  const { sectionId, on, onToggle, children, className } = props;
  const t = useUiText();
  const [penTouched, setPenTouched] = useState(false);
  const load = useDeferredLoad(on || penTouched);
  const [idle, setIdle] = useState(false);
  const [status, setStatus] = useState<HTMLElement | null>(null);
  const [notice, setNotice] = useState<HTMLElement | null>(null);
  const [after, setAfter] = useState<HTMLElement | null>(null);
  const [host, setHost] = useState<HTMLElement | null>(null);
  const onPointerDown = useCallback((e: PointerEvent) => {
    if (e.pointerType === "pen") setPenTouched(true);
  }, []);
  const slots = status && notice && after && host ? { status, notice, after, host } : null;

  return (
    <div className={cx("flex flex-col gap-4", className)} data-section-ink="" data-ink-idle={idle ? "" : undefined}>
      <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <Button
          variant="secondary"
          aria-pressed={on}
          onClick={() => onToggle(!on)}
          icon={<PenLine size={16} />}
          className="min-h-11 px-4"
        >
          {on ? t("ink.page.toggle_on") : t("ink.page.toggle")}
        </Button>
        <div ref={setStatus} className="contents" />
      </div>
      <div ref={setNotice} className="contents" />
      <section
        aria-label={t("ink.page.surface")}
        data-ink-surface=""
        style={{ position: "relative", isolation: "isolate" }}
        onPointerDown={load ? undefined : onPointerDown}
      >
        {children}
        {/* Never takes input itself: the engine's layer turns pointer events on only while ink is on. */}
        <div ref={setHost} data-ink-host="" style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
      </section>
      <div ref={setAfter} className="contents" />
      {load && slots ? (
        <Suspense fallback={null}>
          <SectionInkLive key={sectionId} {...props} slots={slots} onIdle={setIdle} />
        </Suspense>
      ) : null}
    </div>
  );
}
