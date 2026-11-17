import { lazy, type ReactNode, Suspense, useEffect, useRef, useState } from "react";
import type { TextDecorator } from "../content/types";
import type { TextRun } from "./anchor";
import type { SelectionInfo } from "./SelectionToolbar";

/** Highlight data, the selection toolbar and the offline queue: loaded once the content has painted. */
const HighlightEngine = lazy(() => import("./HighlightEngine"));

export interface HighlightableTextProps {
  anchorType: "section" | "question";
  anchorId: string;
  /** The surface's text runs (sectionRuns for a section body, htmlTextRuns for an explanation). */
  runs: readonly TextRun[];
  /**
   * Renders the content. `decorate` is undefined until the highlights are loaded: the content is on screen first,
   * then repainted with the student's highlights (same elements, nothing remounts).
   */
  children: (decorate: TextDecorator | undefined) => ReactNode;
  onNote: (sel: SelectionInfo) => void;
  onCard?: (sel: SelectionInfo) => void;
  onAsk?: (sel: SelectionInfo) => void;
  /** Off while ink is on (the Pencil draws instead of selecting). */
  enabled?: boolean;
  /** The surface shows a device copy: do not repair anchors against it. */
  offline?: boolean;
  toolbarLabel?: string;
}

/** Run `fn` once the browser has painted and is idle (a short timeout where there is no idle callback). */
function afterFirstPaint(fn: () => void): () => void {
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  if (w.requestIdleCallback) {
    const id = w.requestIdleCallback(fn, { timeout: 600 });
    return () => w.cancelIdleCallback?.(id);
  }
  const id = window.setTimeout(fn, 120);
  return () => window.clearTimeout(id);
}

/**
 * A text surface the student can highlight (segment 13). It renders its content straight away; the highlight
 * engine (TipTap-free, but with the notes API, the offline queue and the selection toolbar) loads after the first
 * paint and then paints the student's highlights and opens the toolbar on a selection.
 */
export function HighlightableText({ children, ...engine }: HighlightableTextProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [decorate, setDecorate] = useState<TextDecorator | undefined>(undefined);
  useEffect(() => afterFirstPaint(() => setReady(true)), []);
  // A function in state: wrap it, or React would call it as an updater.
  const [onDecorate] = useState(() => (d: TextDecorator) => setDecorate(() => d));

  return (
    <div ref={ref} className="relative" data-highlightable={engine.anchorType}>
      {children(decorate)}
      {ready ? (
        <Suspense fallback={null}>
          <HighlightEngine {...engine} hostRef={ref} onDecorate={onDecorate} />
        </Suspense>
      ) : null}
    </div>
  );
}
