import type { InkEngine, InkEngineState } from "@naadi/ink";
import { Button, cx } from "@naadi/ui";
import { PenLine, TriangleAlert } from "lucide-react";
import { type ReactNode, useCallback, useRef, useState } from "react";
import { useUiText } from "../../copy/useUiText";
import { InkCanvas } from "../InkCanvas";
import { InkToolbar } from "../InkToolbar";
import { useBlockAnchors } from "./blockAnchors";
import { InkSaveStatus } from "./InkSaveStatus";
import { useInkSettings } from "./settings";
import { useInkLayer } from "./useInkLayer";

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
  className?: string;
}

/**
 * Ink on a Reader section (segment 12b): persisted and synced per section (`section` surface), anchored to the
 * paragraph under each stroke so it survives reflow between iPad and phone, with the save state as icon + word and
 * a notice when the section text changed after the ink was drawn (the ink is never dropped).
 */
export function SectionInk({ sectionId, doc, on, onToggle, offline, children, className }: SectionInkProps) {
  const t = useUiText();
  const engine = useRef<InkEngine | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<InkEngineState | null>(null);
  const { settings, save } = useInkSettings();
  const layer = useInkLayer("section", sectionId, { engineRef: engine, offline });
  const anchors = useBlockAnchors(surface, doc);
  const onState = useCallback((s: InkEngineState) => setState(s), []);

  return (
    <div className={cx("flex flex-col gap-4", className)}>
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
        <InkSaveStatus layer={layer} />
      </div>
      {layer.outdated > 0 ? (
        <p
          role="note"
          data-ink-outdated=""
          className="m-0 flex items-start gap-2 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm"
        >
          <TriangleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
          {t("ink.page.outdated")}
        </p>
      ) : null}
      <div ref={surface}>
        <InkCanvas
          key={sectionId}
          {...layer.canvasProps}
          enabled={on}
          surfaceType="section"
          theme="dark"
          anchors={anchors}
          penOnly={settings.pen_only}
          predict={settings.predict}
          tool={settings.tool}
          color={settings.color}
          onStateChange={onState}
          label={t("ink.page.surface")}
        >
          {children}
        </InkCanvas>
      </div>
      {on && state ? (
        <div className="sticky bottom-[calc(var(--shell-bottom,0px)+12px)] z-10">
          <InkToolbar
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
        </div>
      ) : null}
    </div>
  );
}
