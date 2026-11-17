import { Link } from "@tanstack/react-router";
import { Unlink } from "lucide-react";
import { type RefObject, useEffect, useMemo } from "react";
import type { TextDecorator } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { useNotesSync, useNoteWriter } from "../notes/api";
import { fingerprint, flatten, type HighlightColor, type TextRun } from "./anchor";
import { useHighlightDecorator } from "./Mark";
import { PendingStatus } from "./PendingStatus";
import { type SelectionInfo, SelectionToolbar, type ToolbarActions } from "./SelectionToolbar";
import { useAnchoredHighlights } from "./useAnchoredHighlights";

export interface HighlightEngineProps {
  anchorType: "section" | "question";
  anchorId: string;
  /** The surface's text runs (sectionRuns for a section body, htmlTextRuns for an explanation). */
  runs: readonly TextRun[];
  /** The element the content is rendered in (the toolbar is placed against it). */
  hostRef: RefObject<HTMLDivElement | null>;
  /** The decorator that paints the highlights (the shell hands it to the content). */
  onDecorate: (decorate: TextDecorator) => void;
  onNote: (sel: SelectionInfo) => void;
  onCard?: (sel: SelectionInfo) => void;
  onAsk?: (sel: SelectionInfo) => void;
  /** Off while ink is on (the Pencil draws instead of selecting). */
  enabled?: boolean;
  /** The surface shows a device copy: do not repair anchors against it. */
  offline?: boolean;
  toolbarLabel?: string;
}

/**
 * The highlight engine of a text surface (segment 13), loaded after first paint by `HighlightableText`: paints the
 * student's highlights (located by quote when the text changed), opens the selection toolbar, saves new highlights
 * through the offline queue, and says when some could not be placed (they stay in Notes as Detached, never dropped).
 */
export default function HighlightEngine({
  anchorType,
  anchorId,
  runs,
  hostRef,
  onDecorate,
  onNote,
  onCard,
  onAsk,
  enabled = true,
  offline,
  toolbarLabel,
}: HighlightEngineProps) {
  const t = useUiText();
  const revision = useMemo(() => fingerprint(flatten(runs).text), [runs]);
  const hl = useAnchoredHighlights(anchorType, anchorId, runs, revision, { repair: !offline });
  const writer = useNoteWriter();
  // Send what this device queued (now, on reconnect, every 30 s while something waits).
  useNotesSync();
  const decorate = useHighlightDecorator(hl.ranges, null);
  useEffect(() => onDecorate(decorate), [decorate, onDecorate]);

  const colors = useMemo(() => new Map(hl.ranges.map((r) => [r.id, r.color])), [hl.ranges]);
  const actions: ToolbarActions = {
    onHighlight: (color: HighlightColor, sel) => {
      writer.create({
        kind: "highlight",
        anchor_type: anchorType,
        anchor_id: anchorId,
        anchor_pos: sel.anchor,
        highlight_color: color,
      });
    },
    onNote,
    onCard,
    onAsk,
    onRecolor: (id, color) => writer.update(id, { highlight_color: color }),
    onRemove: (id) => writer.remove(id),
    colorOf: (id) => colors.get(id) ?? null,
  };
  const ids = useMemo(() => [...hl.notes.map((n) => n.id)], [hl.notes]);

  return (
    <>
      <SelectionToolbar
        surfaceRef={hostRef}
        hostRef={hostRef}
        runs={runs}
        actions={actions}
        enabled={enabled}
        label={toolbarLabel}
      />
      {hl.detached.length > 0 ? (
        <p
          role="note"
          data-detached-notice=""
          className="mt-5 mb-0 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm"
        >
          <Unlink aria-hidden="true" size={16} className="shrink-0" />
          <span>
            {hl.detached.length === 1
              ? t("highlights.detached_one")
              : t("highlights.detached_many", { n: String(hl.detached.length) })}
          </span>
          <Link to="/notes" search={{ anchor: "detached" }} className="min-h-11 py-3">
            {t("highlights.detached_link")}
          </Link>
        </p>
      ) : null}
      <PendingStatus ids={ids} className="mt-3" />
    </>
  );
}
