import type { InkEngine, InkEngineState } from "@naadi/ink";
import { cx } from "@naadi/ui";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, UNREACHABLE } from "../auth/apiError";
import { InkCanvas } from "../ink/InkCanvas";
import { StickyInkToolbar } from "../ink/InkToolbar";
import { InkSaveStatus } from "../ink/persist/InkSaveStatus";
import { useInkSettings } from "../ink/persist/settings";
import type { InkTransport } from "../ink/persist/sync";
import { apiTransport } from "../ink/persist/transport";
import { useInkLayer } from "../ink/persist/useInkLayer";
import { PaperBackground } from "./PaperBackground";
import type { Paper } from "./paper";

export interface PageInkProps {
  /** The note the ink belongs to (a notebook page, or the Reader's section note): `notebook_page` surface id. */
  noteId: string;
  paper: Paper;
  /** Ink on: the page takes Pencil input and the toolbar shows. */
  on: boolean;
  onToggle: (on: boolean) => void;
  /** Accessible name of the page ("Notebook page 4"). */
  label: string;
  /** The page already knows the server is unreachable (device copy): the ink is read from the device too. */
  offline?: boolean;
  /** Where the toolbar sticks while scrolling (default bottom, like the Notebook board). */
  toolbar?: "top" | "bottom";
  /**
   * The note itself is not on the server yet (made on this device, its create still queued): the ink is kept on
   * the device ("will sync") and sent once the note exists, instead of being refused by the ink API.
   */
  pending?: boolean;
  /** Shown next to the save state (e.g. the "Linked: Chapter …" chip). */
  aside?: ReactNode;
  className?: string;
}

/**
 * A US Letter paper page with persisted ink (segment 13): the paper template under the ink, ink through
 * `useInkLayer('notebook_page', noteId)` with the 12a tools (pen, marker, eraser, lasso, shapes, text; no image
 * tool on paper pages), the save state as icon + word. The page keeps the Letter ratio at any width, so ink
 * stored in surface units lands on the same lines on iPad, phone and in the PDF export.
 */
export function PageInk({
  noteId,
  paper,
  on,
  onToggle,
  label,
  offline,
  pending = false,
  toolbar = "bottom",
  aside,
  className,
}: PageInkProps) {
  const engine = useRef<InkEngine | null>(null);
  const [state, setState] = useState<InkEngineState | null>(null);
  const { settings, save } = useInkSettings();
  const held = useRef(pending);
  held.current = pending;
  // The ink API, held back (as if offline: nothing is lost, it waits on the device) while the note is pending.
  const transport = useMemo<InkTransport>(() => {
    const real = apiTransport("notebook_page", noteId);
    const hold = () => {
      if (held.current) throw new ApiError(UNREACHABLE, null, null, null);
    };
    return {
      load: async (since) => {
        hold();
        return real.load(since);
      },
      send: async (rev, items, opts) => {
        hold();
        return real.send(rev, items, opts);
      },
      beacon: (rev, items) => !held.current && real.beacon(rev, items),
      abort: () => real.abort?.(),
    };
  }, [noteId]);
  const layer = useInkLayer("notebook_page", noteId, { engineRef: engine, offline: offline || pending, transport });
  const retry = layer.retry;
  const wasPending = useRef(pending);
  useEffect(() => {
    // The note now exists on the server: send what waited and load the layer.
    if (wasPending.current && !pending) retry();
    wasPending.current = pending;
  }, [pending, retry]);
  const onState = useCallback((s: InkEngineState) => setState(s), []);

  const bar =
    on && state ? (
      <StickyInkToolbar
        placement={toolbar}
        state={state}
        theme="paper"
        onEnabled={onToggle}
        onTool={(tool) => {
          engine.current?.setTool(tool);
          save({ tool }, { debounceMs: 1500 });
        }}
        onColor={(color) => {
          engine.current?.setColor(color);
          save({ color }, { debounceMs: 1500 });
        }}
        onEraserMode={(mode) => engine.current?.setEraserMode(mode)}
        onUndo={() => engine.current?.undo()}
        onRedo={() => engine.current?.redo()}
        onClear={() => engine.current?.clear()}
      />
    ) : null;

  return (
    <div
      className={cx("flex flex-col gap-3", className)}
      data-page-ink={noteId}
      data-ink-idle={layer.idle ? "" : undefined}
      data-ink-ready={layer.ready ? "" : undefined}
    >
      <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <InkSaveStatus layer={layer} />
        {aside}
      </div>
      {toolbar === "top" ? bar : null}
      <InkCanvas
        key={noteId}
        {...layer.canvasProps}
        enabled={on}
        surfaceType="notebook_page"
        theme="paper"
        penOnly={settings.pen_only}
        predict={settings.predict}
        tool={settings.tool}
        color={settings.color}
        onStateChange={onState}
        label={label}
        className="overflow-hidden rounded-[18px] bg-paper text-ground shadow-[0_0_0_1px_var(--color-line-strong)]"
      >
        <PaperBackground paper={paper} />
      </InkCanvas>
      {toolbar === "bottom" ? bar : null}
    </div>
  );
}
