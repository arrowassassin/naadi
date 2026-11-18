import type { InkEngine, InkEngineState } from "@naadi/ink";
import { Button } from "@naadi/ui";
import { Sparkles, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useUiText } from "../../copy/useUiText";
import { InkCanvas } from "../InkCanvas";
import { StickyInkToolbar } from "../InkToolbar";
import { useBlockAnchors } from "./blockAnchors";
import { InkSaveStatus } from "./InkSaveStatus";
import { hasOpaqueBlocks, type SectionInkProps, type SectionInkSlots } from "./SectionInk";
import { useInkSettings } from "./settings";
import { useInkLayer } from "./useInkLayer";

/**
 * The live half of SectionInk (its own chunk): the engine on the page's existing ink host, persistence and sync
 * (useInkLayer: Dexie + the API), block anchors, the save state, the "section updated" notice and the toolbar,
 * each rendered into the slot the wrapper laid out before this code arrived.
 */
export function SectionInkLive({
  sectionId,
  doc,
  on,
  onToggle,
  offline,
  onAsk,
  slots,
  onIdle,
}: SectionInkProps & { slots: SectionInkSlots; onIdle: (idle: boolean) => void }) {
  const t = useUiText();
  const engine = useRef<InkEngine | null>(null);
  const [state, setState] = useState<InkEngineState | null>(null);
  const { settings, save } = useInkSettings();
  const layer = useInkLayer("section", sectionId, { engineRef: engine, offline });
  const surface = useMemo(() => ({ current: slots.host.parentElement }), [slots.host]);
  const anchors = useBlockAnchors(surface, doc);
  const onState = useCallback((s: InkEngineState) => setState(s), []);
  useEffect(() => onIdle(layer.idle), [layer.idle, onIdle]);
  useEffect(() => () => onIdle(false), [onIdle]);

  return (
    <>
      {createPortal(<InkSaveStatus layer={layer} />, slots.status)}
      {layer.outdated > 0
        ? createPortal(
            <p
              role="note"
              data-ink-outdated=""
              className="m-0 flex items-start gap-2 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm"
            >
              <TriangleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
              {t("ink.page.outdated")}
            </p>,
            slots.notice,
          )
        : null}
      <InkCanvas
        {...layer.canvasProps}
        coordinateColumn={surface.current?.querySelector<HTMLElement>("[data-ink-column]")}
        host={slots.host}
        enabled={on}
        surfaceType="section"
        hasOpaqueBlocks={hasOpaqueBlocks(doc)}
        theme="dark"
        anchors={anchors}
        penOnly={settings.pen_only}
        predict={settings.predict}
        tool={settings.tool}
        color={settings.color}
        onStateChange={onState}
        onAsk={onAsk}
        label={t("ink.page.surface")}
      />
      {createPortal(
        <>
          {on && state?.selection && onAsk ? (
            <Button
              variant="assistant"
              icon={<Sparkles size={16} />}
              className="min-h-11 self-start"
              onClick={() => void engine.current?.askAboutSelection()}
            >
              {t("ink.select.ask")}
            </Button>
          ) : null}
          {on && state ? (
            <StickyInkToolbar
              placement="bottom"
              state={state}
              theme="dark"
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
          ) : null}
        </>,
        slots.after,
      )}
    </>
  );
}
