import type { InkColor, InkDiff, InkItem, InkSurfaceType } from "@naadi/shared";
import { type CSSProperties, type ReactNode, type Ref, useEffect, useRef } from "react";
import type { InkAnchorResolver } from "../anchors";
import {
  type EraserMode,
  InkEngine,
  type InkEngineState,
  type InkError,
  type InkLabels,
  type InkSelectionPayload,
  type InkToolName,
  type MarkerMode,
  markerModeFor,
  type SelectionInfo,
  type ToolSizes,
} from "../engine";
import type { LatencyRecorder } from "../latency";
import type { InkTheme, Palette } from "../render";

export interface InkSurfaceProps {
  /** Initial items; changing the array identity reloads the layer (clears undo history). */
  items?: readonly InkItem[];
  /** Ink is on (the surface takes pen/finger input). Required: a surface never captures input by accident. */
  enabled: boolean;
  /** What the surface shows; picks the marker placement (see markerModeFor) unless `markerMode` is set. */
  surfaceType?: InkSurfaceType;
  /** The content has figures, images or tables with fills (opaque blocks): markers multiply over it. */
  hasOpaqueBlocks?: boolean;
  markerMode?: MarkerMode;
  tool?: InkToolName;
  color?: InkColor;
  eraserMode?: EraserMode;
  penOnly?: boolean;
  predict?: boolean;
  theme?: InkTheme;
  palette?: Partial<Palette>;
  sizes?: Partial<ToolSizes>;
  latency?: LatencyRecorder;
  /** Optional text column defining 1 su and x=0. The canvas still covers the full host, including both margins. */
  coordinateColumn?: HTMLElement | null;
  /** Reflow anchoring for text surfaces; a new resolver re-places anchored items. */
  anchors?: InkAnchorResolver | null;
  /**
   * Attach to this element instead of rendering one (12b): the page already rendered the content and an empty host
   * (position absolute, inset 0, pointer-events none, inside a relative container) before the engine's code
   * loaded, so the content is not remounted when ink arrives. With `host`, InkSurface renders nothing and
   * `children`, `label`, `className` and `style` are the page's business. Read at mount.
   */
  host?: HTMLElement | null;
  /** Clock for updatedAt stamps (epoch ms; read at mount). Sync passes a server-corrected clock (12b). */
  now?: () => number;
  onStrokesChange?: (diff: InkDiff) => void;
  /** A lasso closed; `png` is an ink-only picture, null when the loop selected no ink (region only). */
  onLasso?: (selection: SelectionInfo, png: Blob | null) => void;
  onConvertToText?: (payload: InkSelectionPayload) => void;
  onAsk?: (payload: InkSelectionPayload) => void;
  onStateChange?: (state: InkEngineState) => void;
  onPenActiveChange?: (active: boolean) => void;
  onError?: (error: InkError) => void;
  labels: InkLabels;
  /** Imperative handle: undo/redo/clear/selection actions. */
  engineRef?: Ref<InkEngine | null>;
  /** The content the ink sits on. */
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  /** Accessible name of the surface region. */
  label: string;
}

/**
 * React binding: renders the content with an ink layer over it and keeps the engine in sync with props.
 * The engine is created once per mount; callbacks are read through refs so they can change freely.
 */
export function InkSurface(props: InkSurfaceProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const engine = useRef<InkEngine | null>(null);
  const cb = useRef(props);
  cb.current = props;

  // The engine is created once per mount; the effects below keep it in sync with props.
  useEffect(() => {
    const host = cb.current.host ?? hostRef.current;
    if (!host) return;
    const p = cb.current;
    const e = new InkEngine({
      host,
      items: p.items,
      enabled: p.enabled,
      markerMode: p.markerMode ?? markerModeFor(p.surfaceType ?? "section", { hasOpaqueBlocks: p.hasOpaqueBlocks }),
      tool: p.tool,
      color: p.color,
      eraserMode: p.eraserMode,
      penOnly: p.penOnly,
      predict: p.predict,
      theme: p.theme,
      palette: p.palette,
      sizes: p.sizes,
      latency: p.latency,
      coordinateColumn: p.coordinateColumn,
      anchors: p.anchors ?? null,
      // Read through the ref, so a corrected clock that arrives after mount is used.
      now: () => (cb.current.now ?? Date.now)(),
      onStrokesChange: (d) => cb.current.onStrokesChange?.(d),
      onLasso: (s, png) => cb.current.onLasso?.(s, png),
      onConvertToText: (x) => cb.current.onConvertToText?.(x),
      onAsk: (x) => cb.current.onAsk?.(x),
      onStateChange: (s) => cb.current.onStateChange?.(s),
      onPenActiveChange: (a) => cb.current.onPenActiveChange?.(a),
      onError: (m) => cb.current.onError?.(m),
      // Read through the ref, so labels configured after mount (ui_copy loading) are used.
      get labels() {
        return cb.current.labels;
      },
    });
    engine.current = e;
    if (typeof p.engineRef === "function") p.engineRef(e);
    else if (p.engineRef) (p.engineRef as { current: InkEngine | null }).current = e;
    return () => {
      e.destroy();
      engine.current = null;
      const r = cb.current.engineRef;
      if (typeof r === "function") r(null);
      else if (r) (r as { current: InkEngine | null }).current = null;
    };
  }, []);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (props.items) engine.current?.load(props.items);
  }, [props.items]);
  useEffect(() => engine.current?.setEnabled(props.enabled), [props.enabled]);
  const firstAnchors = useRef(true);
  useEffect(() => {
    if (firstAnchors.current) {
      firstAnchors.current = false;
      return;
    }
    engine.current?.setAnchors(props.anchors ?? null);
  }, [props.anchors]);
  useEffect(
    () =>
      engine.current?.setMarkerMode(
        props.markerMode ?? markerModeFor(props.surfaceType ?? "section", { hasOpaqueBlocks: props.hasOpaqueBlocks }),
      ),
    [props.markerMode, props.surfaceType, props.hasOpaqueBlocks],
  );
  useEffect(() => {
    if (props.tool) engine.current?.setTool(props.tool);
  }, [props.tool]);
  useEffect(() => {
    if (props.color) engine.current?.setColor(props.color);
  }, [props.color]);
  useEffect(() => {
    if (props.eraserMode) engine.current?.setEraserMode(props.eraserMode);
  }, [props.eraserMode]);
  useEffect(() => engine.current?.setPenOnly(props.penOnly ?? true), [props.penOnly]);
  useEffect(() => engine.current?.setPredict(props.predict ?? true), [props.predict]);
  useEffect(() => {
    if (props.theme) engine.current?.setTheme(props.theme, props.palette);
  }, [props.theme, props.palette]);

  if (props.host !== undefined) return null;
  return (
    <section
      aria-label={props.label}
      className={props.className}
      style={{ position: "relative", isolation: "isolate", ...props.style }}
      data-ink-surface=""
    >
      {props.children}
      {/* The host never takes input itself: the engine's layer turns pointer events on only while ink is on, so
          links, buttons and figures under the ink stay usable when it is off. */}
      <div ref={hostRef} data-ink-host="" style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
    </section>
  );
}
