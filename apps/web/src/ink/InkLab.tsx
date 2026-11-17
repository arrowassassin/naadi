import {
  canvasBudget,
  DEFAULT_SIZES,
  type EraserMode,
  type InkEngine,
  type InkEngineState,
  type InkError,
  type InkSelectionPayload,
  type InkTheme,
  type InkToolName,
  inkProbe,
  inputCapabilities,
  LatencyRecorder,
  parkedPictureBudget,
  type SampleStats,
  type SelectionInfo,
  slabBudget,
} from "@naadi/ink";

import type { InkColor, InkDiff, InkItem } from "@naadi/shared";
import { Button, Chip, cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import type React from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { type InkCopy, inkErrorText, useInkCopy } from "./copy";
import { InkCanvas } from "./InkCanvas";
import { StickyInkToolbar } from "./InkToolbar";
import "./ink-lab.css";
import { measureScroll, type ScrollMeasurement, stressStrokes } from "./stress";

interface LabEvent {
  n: number;
  kind: "strokes" | "lasso" | "convert" | "ask" | "error";
  text: string;
}

declare global {
  interface Window {
    /** Read-only handle for the e2e suite and manual debugging on this dev page only. */
    naadiInkLab?: {
      page: () => InkEngine | null;
      stress: () => InkEngine | null;
      pages: () => (InkEngine | null)[];
      latency: LatencyRecorder;
      budget: typeof canvasBudget;
      slabs: typeof slabBudget;
      parked: typeof parkedPictureBudget;
      probe: typeof inkProbe;
    };
  }
}

const STRESS_COUNT = 5000;
const STRESS_HEIGHT_SU = 4;

/**
 * The stress page's height in surface widths: 4, or `?stress_height=N` (2-16). A taller page (the e2e
 * suite uses 12 at dpr 2) is more than the engine keeps as slabs, so slabs are released and come back.
 */
function stressHeightSu(): number {
  const n = Number(new URLSearchParams(window.location.search).get("stress_height"));
  return Number.isFinite(n) && n >= 2 && n <= 16 ? n : STRESS_HEIGHT_SU;
}
const MANY_PAGES = 12;
const MANY_PAGE_STROKES = 400;

function SectionHeading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="m-0 font-mono text-label font-medium text-text-3 uppercase">
      {children}
    </h2>
  );
}

function Switch({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
}) {
  const t = useInkCopy();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cx(
        "inline-flex min-h-11 items-center gap-2.5 rounded-pill border px-4 text-[0.8125rem] transition-colors duration-(--duration-control) ease-out",
        checked ? "border-paper text-text" : "border-line-control text-text-2 hover:bg-surface-2",
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          "relative inline-block h-5 w-9 rounded-pill transition-colors duration-(--duration-control)",
          checked ? "bg-paper" : "bg-control",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-4 rounded-full transition-[left] duration-(--duration-control)",
            checked ? "left-[18px] bg-ground" : "left-0.5 bg-text-3",
          )}
        />
      </span>
      {label}
      <span className="font-mono text-label-sm text-text-3 uppercase">
        {t(checked ? "ink.lab.switch_on" : "ink.lab.switch_off")}
      </span>
      {hint ? <span className="sr-only">{hint}</span> : null}
    </button>
  );
}

/** A live frame counter for the 240 fps slow-motion protocol: each video frame shows which rAF it is. */
function FrameCounter() {
  const t = useInkCopy();
  const ref = useRef<HTMLSpanElement>(null);
  // The frame number and the time since the previous frame, e.g. "000042 · 16.7 ms".
  const label = (n: number, ms: number) =>
    `${String(n).padStart(6, "0")} · ${t("ink.lab.ms", { value: ms.toFixed(1) })}`;
  const labelRef = useRef(label);
  labelRef.current = label;
  useEffect(() => {
    let raf = 0;
    let n = 0;
    let prev = performance.now();
    const tick = (ts: number) => {
      n++;
      if (ref.current) ref.current.textContent = labelRef.current(n, ts - prev);
      prev = ts;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <p className="m-0 font-mono text-[1.5rem] text-text" aria-live="off">
      <span className="sr-only">{`${t("ink.lab.frame_counter")} `}</span>
      <span ref={ref}>{label(0, 0)}</span>
    </p>
  );
}

/**
 * Actions for a lasso selection, placed beside it and never over it: below when there is room inside the
 * page, otherwise above. With no ink in the loop only "Ask Naadi" applies, so only it is shown.
 */
function SelectionMenu({
  selection,
  box,
  engine,
}: {
  selection: SelectionInfo;
  box: React.RefObject<HTMLDivElement | null>;
  engine: React.RefObject<InkEngine | null>;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const hasInk = selection.ids.length > 0;
  const t = useInkCopy();
  useLayoutEffect(() => {
    const el = menu.current;
    if (el && (el.offsetWidth !== size.w || el.offsetHeight !== size.h))
      setSize({ w: el.offsetWidth, h: el.offsetHeight });
  });
  const surfaceW = box.current?.clientWidth ?? 0;
  const surfaceH = box.current?.clientHeight ?? 0;
  const r = selection.rect;
  const gap = 12;
  const left = Math.max(8, Math.min(r.left, surfaceW - size.w - 8));
  const below = r.top + r.height + gap;
  const above = r.top - size.h - gap;
  const top = below + size.h <= surfaceH - 8 || above < 8 ? below : above;
  return (
    <div
      ref={menu}
      role="toolbar"
      aria-label={t("ink.select.menu")}
      className="absolute z-20 flex max-w-[calc(100%-16px)] flex-wrap gap-1 rounded-[28px] border border-line-strong bg-ground p-1.5"
      style={{ left: `${left}px`, top: `${top}px`, visibility: size.w ? "visible" : "hidden" }}
    >
      <span className="sr-only">{t(hasInk ? "ink.select.drag_hint" : "ink.select.region_hint")}</span>
      {hasInk ? (
        <>
          <Button variant="secondary" className="min-h-11" onClick={() => engine.current?.duplicateSelection()}>
            {t("ink.select.copy")}
          </Button>
          <Button variant="secondary" className="min-h-11" onClick={() => engine.current?.deleteSelection()}>
            {t("ink.select.delete")}
          </Button>
          <Button variant="secondary" className="min-h-11" onClick={() => void engine.current?.convertSelection()}>
            {t("ink.select.convert")}
          </Button>
        </>
      ) : null}
      <Button variant="assistant" className="min-h-11" onClick={() => void engine.current?.askAboutSelection()}>
        {t("ink.select.ask")}
      </Button>
    </div>
  );
}

function fmt(t: InkCopy, ms: number): string {
  return t("ink.lab.ms", { value: ms.toFixed(1) });
}

export function InkLab() {
  const latency = useMemo(() => new LatencyRecorder(), []);
  const caps = useMemo(() => inputCapabilities(), []);
  const pageEngine = useRef<InkEngine | null>(null);
  const stressEngine = useRef<InkEngine | null>(null);
  const [state, setState] = useState<InkEngineState | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [tool, setTool] = useState<InkToolName>("pen");
  const [color, setColor] = useState<InkColor>("ink");
  const [eraserMode, setEraserMode] = useState<EraserMode>("stroke");
  const [penOnly, setPenOnly] = useState(true);
  const [predict, setPredict] = useState(true);
  const [theme, setTheme] = useState<InkTheme>("paper");
  const [slowMo, setSlowMo] = useState(false);
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [png, setPng] = useState<{ url: string; w: number; h: number } | null>(null);
  const [stressItems, setStressItems] = useState<InkItem[] | null>(null);
  const [stressCount, setStressCount] = useState(0);
  const [measuring, setMeasuring] = useState(false);
  const [scroll, setScroll] = useState<ScrollMeasurement | null>(null);
  const [summary, setSummary] = useState(() => latency.summary());
  const [stats, setStats] = useState<Partial<SampleStats>>({});
  const [error, setError] = useState<string | null>(null);
  const eventN = useRef(0);
  const stressRef = useRef<HTMLDivElement>(null);
  const [content, setContent] = useState<"text" | "table">("text");
  const surfaceBox = useRef<HTMLDivElement>(null);
  const [manyPages, setManyPages] = useState<InkItem[][] | null>(null);
  const pageEngines = useRef<(InkEngine | null)[]>([]);
  const [budget, setBudget] = useState(() => canvasBudget.stats());
  const [stressHeight] = useState(stressHeightSu);
  const t = useInkCopy();

  useEffect(() => {
    window.naadiInkLab = {
      page: () => pageEngine.current,
      stress: () => stressEngine.current,
      pages: () => pageEngines.current,
      latency,
      budget: canvasBudget,
      slabs: slabBudget,
      parked: parkedPictureBudget,
      probe: inkProbe,
    };
    return () => {
      window.naadiInkLab = undefined;
    };
  }, [latency]);

  // The panel refreshes at most at 4 Hz, and only when a number changed (an idle page does no work);
  // the recorder itself takes every sample.
  useEffect(() => {
    let last = "";
    const id = window.setInterval(() => {
      // Never re-render the lab while a stroke is being drawn: the panel would compete with the ink it measures.
      if (pageEngine.current?.drawing || stressEngine.current?.drawing) return;
      const summary = latency.summary();
      const stats = { ...(pageEngine.current?.stats ?? {}) };
      const mem = canvasBudget.stats();
      const key = JSON.stringify([summary, stats, mem]);
      if (key === last) return;
      last = key;
      setSummary(summary);
      setStats(stats);
      setBudget(mem);
    }, 250);
    return () => window.clearInterval(id);
  }, [latency]);

  useEffect(() => () => (png ? URL.revokeObjectURL(png.url) : undefined), [png]);

  const log = useCallback((kind: LabEvent["kind"], text: string) => {
    eventN.current += 1;
    const n = eventN.current;
    setEvents((prev) => [{ n, kind, text }, ...prev].slice(0, 8));
  }, []);

  const showPng = useCallback((blob: Blob, sel: SelectionInfo) => {
    setPng({ url: URL.createObjectURL(blob), w: Math.round(sel.rect.width), h: Math.round(sel.rect.height) });
  }, []);

  const onDiff = useCallback(
    (d: InkDiff) =>
      log(
        "strokes",
        t("ink.lab.ev.strokes", { added: d.added.length, updated: d.updated.length, removed: d.removed.length }),
      ),
    [log, t],
  );
  const onLasso = useCallback(
    (sel: SelectionInfo, blob: Blob | null) => {
      if (!blob) {
        log("lasso", t("ink.lab.ev.region", { w: Math.round(sel.rect.width), h: Math.round(sel.rect.height) }));
        return;
      }
      log(
        "lasso",
        t(sel.ids.length === 1 ? "ink.lab.ev.lasso_one" : "ink.lab.ev.lasso_other", {
          count: sel.ids.length,
          kb: Math.round(blob.size / 1024),
        }),
      );
      showPng(blob, sel);
    },
    [log, showPng, t],
  );
  const onConvert = useCallback(
    (p: InkSelectionPayload) =>
      log(
        "convert",
        t("ink.lab.ev.convert", { count: p.ids.length, w: Math.round(p.rect.width), h: Math.round(p.rect.height) }),
      ),
    [log, t],
  );
  const onAsk = useCallback(
    (p: InkSelectionPayload) =>
      log(
        "ask",
        t(p.png ? "ink.lab.ev.ask" : "ink.lab.ev.ask_region", {
          count: p.ids.length,
          w: Math.round(p.rect.width),
          h: Math.round(p.rect.height),
          points: p.path.length,
        }),
      ),
    [log, t],
  );
  const onError = useCallback(
    (e: InkError) => {
      const m = inkErrorText(t, e);
      setError(m);
      log("error", m);
    },
    [log, t],
  );

  const buildStress = () => {
    const items = stressStrokes(STRESS_COUNT, stressHeight);
    setStressItems(items);
    setStressCount(items.length);
    setScroll(null);
  };

  const runScroll = async () => {
    const el = stressRef.current;
    if (!el) return;
    setMeasuring(true);
    const top = el.getBoundingClientRect().top + window.scrollY;
    const result = await measureScroll(Math.max(0, top - 80), top + el.offsetHeight - window.innerHeight * 0.5, 3000);
    setScroll(result);
    setMeasuring(false);
  };

  const mountManyPages = () => {
    setManyPages(Array.from({ length: MANY_PAGES }, (_, i) => stressStrokes(MANY_PAGE_STROKES, 1180 / 820, 100 + i)));
  };

  const sizes = slowMo ? { ...DEFAULT_SIZES, pen: 6 } : DEFAULT_SIZES;
  const surfaceTheme: InkTheme = slowMo ? "paper" : theme;
  const selection = state?.selection ?? null;
  const yes = (b: boolean) => t(b ? "ink.lab.yes" : "ink.lab.no");
  const stressCountText = STRESS_COUNT.toLocaleString("en");
  const frameSummary = summary.frame;
  const paintSummary = summary.paint;

  return (
    <main
      id="main"
      tabIndex={-1}
      className="mx-auto flex max-w-(--content-max) flex-col gap-6 px-(--gutter) pt-8 pb-24 tablet:pt-12"
    >
      <header className="flex flex-col gap-3">
        <p className="m-0 font-mono text-label text-text-3 uppercase">
          <Link to="/system" className="underline underline-offset-2">
            {t("ink.lab.breadcrumb")}
          </Link>
          {` · ${t("ink.gate.title")}`}
        </p>
        <h1 className="m-0 font-display text-headline">{t("ink.gate.title")}</h1>
        <p className="m-0 max-w-[68ch] text-reading text-text-2">{t("ink.lab.intro", { count: stressCountText })}</p>
        <ul aria-label={t("ink.lab.features")} className="m-0 flex list-none flex-wrap gap-2 p-0">
          <li>
            <Chip tone="meta" srPrefix={t("ink.lab.cap.coalesced_name")}>
              {t("ink.lab.cap.coalesced", { value: yes(caps.coalesced) })}
            </Chip>
          </li>
          <li>
            <Chip tone="meta" srPrefix={t("ink.lab.cap.predicted_name")}>
              {t("ink.lab.cap.predicted", { value: yes(caps.predicted) })}
            </Chip>
          </li>
          <li>
            <Chip tone="meta" srPrefix={t("ink.lab.cap.raw_name")}>
              {t("ink.lab.cap.raw", { value: yes(caps.rawUpdate) })}
            </Chip>
          </li>
          <li data-testid="pen-status">
            <Chip tone="meta" srPrefix={t("ink.lab.cap.pen_name")}>
              {t("ink.lab.cap.pen", {
                value: t(state?.penPresent ? "ink.lab.cap.pen_detected" : "ink.lab.cap.pen_missing"),
              })}
            </Chip>
          </li>
          <li data-testid="finger-status">
            <Chip tone="meta" srPrefix={t("ink.lab.cap.finger_name")}>
              {t("ink.lab.cap.finger", {
                value: t(state?.fingersDraw ? "ink.lab.cap.finger_draws" : "ink.lab.cap.finger_scrolls"),
              })}
            </Chip>
          </li>
        </ul>
      </header>

      {error ? (
        <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
          {error}
        </p>
      ) : null}

      {state ? (
        <StickyInkToolbar
          placement="top"
          state={{ ...state, enabled, tool, color, eraserMode }}
          theme={surfaceTheme}
          onEnabled={setEnabled}
          onTool={setTool}
          onColor={setColor}
          onEraserMode={setEraserMode}
          onUndo={() => pageEngine.current?.undo()}
          onRedo={() => pageEngine.current?.redo()}
          onClear={() => pageEngine.current?.clear()}
        />
      ) : null}

      <section aria-labelledby="ink-settings-h" className="flex flex-col gap-3">
        <SectionHeading id="ink-settings-h">{t("ink.lab.settings")}</SectionHeading>
        <div className="flex flex-wrap gap-2">
          <Switch
            label={t("ink.lab.pen_only")}
            checked={penOnly}
            onChange={setPenOnly}
            hint={t("ink.lab.pen_only_hint")}
          />
          <Switch label={t("ink.lab.predict")} checked={predict} onChange={setPredict} />
          <Switch label={t("ink.lab.slowmo")} checked={slowMo} onChange={setSlowMo} hint={t("ink.lab.slowmo_hint")} />
          <Button
            variant="secondary"
            aria-pressed={theme === "dark"}
            disabled={slowMo}
            onClick={() => setTheme(theme === "paper" ? "dark" : "paper")}
          >
            {t(theme === "paper" ? "ink.lab.theme_paper" : "ink.lab.theme_dark")}
          </Button>
          <Button
            variant="secondary"
            aria-pressed={content === "table"}
            onClick={() => setContent(content === "text" ? "table" : "text")}
          >
            {t(content === "text" ? "ink.lab.content_text" : "ink.lab.content_table")}
          </Button>
        </div>
      </section>

      <section aria-labelledby="ink-page-h" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <SectionHeading id="ink-page-h">{t("ink.lab.page")}</SectionHeading>
          <span className="font-mono text-label-sm text-text-3 uppercase" data-testid="page-count">
            {t(state?.count === 1 ? "ink.lab.items_one" : "ink.lab.items_other", { count: state?.count ?? 0 })}
          </span>
        </div>
        {slowMo ? <FrameCounter /> : null}
        <div className="relative" ref={surfaceBox}>
          <InkCanvas
            label={t("ink.lab.page_label")}
            className={cx(
              "w-full overflow-hidden rounded-tile-sm",
              surfaceTheme === "paper" ? "ink-paper-dotted" : "ink-dark-dotted",
            )}
            style={{ aspectRatio: "820 / 1180" }}
            surfaceType="section"
            hasOpaqueBlocks={content === "table"}
            enabled={enabled}
            tool={tool}
            color={slowMo ? "ink" : color}
            eraserMode={eraserMode}
            penOnly={penOnly}
            predict={predict}
            theme={surfaceTheme}
            sizes={sizes}
            latency={latency}
            engineRef={pageEngine}
            onStateChange={(s) => {
              setState(s);
              setTool(s.tool);
              setColor(s.color);
              setEraserMode(s.eraserMode);
            }}
            onStrokesChange={onDiff}
            onLasso={onLasso}
            onConvertToText={onConvert}
            onAsk={onAsk}
            onError={onError}
          >
            <div
              className={cx(
                "flex flex-col gap-4 p-[6%] text-ui",
                surfaceTheme === "paper" ? "text-ground" : "text-text",
              )}
            >
              <p className="m-0 max-w-[48ch] text-reading">{t("ink.lab.sample_intro")}</p>
              <p className="m-0 py-3" data-testid="plain-row">
                {t("ink.lab.sample_plain")}
              </p>
              {content === "table" ? (
                <table className="w-full max-w-[520px] border-collapse">
                  <tbody>
                    <tr data-testid="opaque-row" className="bg-mark-amber text-ground">
                      <td className="px-3 py-3">{t("ink.lab.sample_table")}</td>
                    </tr>
                  </tbody>
                </table>
              ) : null}
            </div>
          </InkCanvas>
          {selection && enabled && tool === "lasso" ? (
            <SelectionMenu selection={selection} box={surfaceBox} engine={pageEngine} />
          ) : null}
        </div>
      </section>

      <section aria-labelledby="ink-latency-h" className="flex flex-col gap-3" data-testid="latency-panel">
        <SectionHeading id="ink-latency-h">{t("ink.lab.latency")}</SectionHeading>
        <div className="grid grid-cols-1 gap-3 tablet:grid-cols-2">
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 rounded-block border border-line-soft bg-surface-1 p-4">
            <dt className="text-text-3">{t("ink.lab.paint_p50")}</dt>
            <dd className="m-0 font-mono" data-metric="paint-p50">
              {fmt(t, paintSummary.p50)}
            </dd>
            <dt className="text-text-3">{t("ink.lab.paint_p95")}</dt>
            <dd className="m-0 font-mono" data-metric="paint-p95">
              {fmt(t, paintSummary.p95)}
            </dd>
            <dt className="text-text-3">{t("ink.lab.frame_p50")}</dt>
            <dd className="m-0 font-mono" data-metric="frame-p50">
              {fmt(t, frameSummary.p50)}
            </dd>
            <dt className="text-text-3">{t("ink.lab.frame_p95")}</dt>
            <dd className="m-0 font-mono" data-metric="frame-p95">
              {fmt(t, frameSummary.p95)}
            </dd>
            <dt className="text-text-3">{t("ink.lab.frames")}</dt>
            <dd className="m-0 font-mono" data-metric="frames">
              {paintSummary.count}
            </dd>
          </dl>
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 rounded-block border border-line-soft bg-surface-1 p-4">
            <dt className="text-text-3">{t("ink.lab.samples")}</dt>
            <dd className="m-0 font-mono" data-metric="samples">
              {stats.accepted ?? 0}
            </dd>
            <dt className="text-text-3">{t("ink.lab.coalesced_batches")}</dt>
            <dd className="m-0 font-mono" data-metric="coalesced">
              {stats.coalescedBatches ?? 0}
            </dd>
            <dt className="text-text-3">{t("ink.lab.predicted_samples")}</dt>
            <dd className="m-0 font-mono" data-metric="predicted">
              {stats.predicted ?? 0}
            </dd>
            <dt className="text-text-3">{t("ink.lab.id_mismatch")}</dt>
            <dd className="m-0 font-mono" data-metric="id-mismatch">
              {stats.idMismatch ?? 0}
            </dd>
            <dt className="text-text-3">{t("ink.lab.dropped")}</dt>
            <dd className="m-0 font-mono" data-metric="dropped">
              {stats.dropped ?? 0}
            </dd>
          </dl>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => latency.reset()}>
            {t("ink.lab.reset")}
          </Button>
        </div>
        <details className="rounded-block border border-line-soft bg-surface-1 p-4 text-text-2">
          <summary className="min-h-11 cursor-pointer content-center font-semibold text-text">
            {t("ink.lab.protocol")}
          </summary>
          <ol className="mt-2 mb-0 flex flex-col gap-1.5 pl-5">
            {(["1", "2", "3", "4", "5"] as const).map((n) => (
              <li key={n}>{t(`ink.lab.protocol_${n}`)}</li>
            ))}
          </ol>
        </details>
      </section>

      <section aria-labelledby="ink-events-h" className="flex flex-col gap-3">
        <SectionHeading id="ink-events-h">{t("ink.lab.events")}</SectionHeading>
        <div className="grid grid-cols-1 gap-3 tablet:grid-cols-[1fr_auto]">
          {events.length === 0 ? (
            <p className="m-0 text-text-2">{t("ink.lab.events_empty")}</p>
          ) : (
            <ol
              aria-label={t("ink.lab.events_list")}
              className="m-0 flex list-none flex-col gap-1.5 p-0"
              data-testid="events"
            >
              {events.map((ev) => (
                <li key={ev.n} data-kind={ev.kind} className="font-mono text-[0.8125rem] text-text-2">
                  {ev.text}
                </li>
              ))}
            </ol>
          )}
          {png ? (
            <figure className="m-0 flex flex-col gap-1.5">
              <img
                src={png.url}
                alt={t("ink.lab.png_alt")}
                className="max-h-40 max-w-60 rounded-inner border border-line-strong object-contain"
              />
              <figcaption className="font-mono text-label-sm text-text-3 uppercase">
                {t("ink.lab.png_caption", { w: png.w, h: png.h })}
              </figcaption>
            </figure>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="ink-stress-h" className="flex flex-col gap-3" data-testid="stress">
        <SectionHeading id="ink-stress-h">{t("ink.lab.stress")}</SectionHeading>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={buildStress}>
            {t(stressItems ? "ink.lab.stress_rebuild" : "ink.lab.stress_build", { count: stressCountText })}
          </Button>
          <Button variant="secondary" disabled={!stressItems || measuring} onClick={() => void runScroll()}>
            {t(measuring ? "ink.lab.measuring" : "ink.lab.measure")}
          </Button>
          {scroll ? (
            <p className="m-0 font-mono text-[0.8125rem] text-text-2" data-testid="scroll-result" data-fps={scroll.fps}>
              {t("ink.lab.scroll_result", {
                fps: scroll.fps,
                p95: scroll.p95FrameMs,
                long: scroll.longFrames,
                frames: scroll.frames,
              })}
            </p>
          ) : null}
        </div>
        {stressItems ? (
          <div ref={stressRef}>
            <InkCanvas
              label={t("ink.lab.stress_label", { count: stressCountText })}
              className={cx(
                "w-full overflow-hidden rounded-tile-sm",
                surfaceTheme === "paper" ? "bg-paper" : "bg-surface-1",
              )}
              style={{ aspectRatio: `1 / ${stressHeight}` }}
              items={stressItems}
              enabled={enabled}
              tool={tool}
              color={color}
              eraserMode={eraserMode}
              penOnly={penOnly}
              predict={predict}
              theme={surfaceTheme}
              latency={latency}
              engineRef={stressEngine}
              onStateChange={(s) => setStressCount(s.count)}
              onError={onError}
            />
            <p className="mt-2 mb-0 font-mono text-label-sm text-text-3 uppercase" data-testid="stress-count">
              {t("ink.lab.items_other", { count: stressCount.toLocaleString("en") })}
            </p>
          </div>
        ) : null}
      </section>
      <section aria-labelledby="ink-many-h" className="flex flex-col gap-3" data-testid="many-pages">
        <SectionHeading id="ink-many-h">{t("ink.lab.many")}</SectionHeading>
        <p className="m-0 max-w-[68ch] text-text-2">{t("ink.lab.many_body", { count: MANY_PAGES })}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={manyPages !== null} onClick={mountManyPages}>
            {t("ink.lab.many_mount", { count: MANY_PAGES })}
          </Button>
          <p className="m-0 font-mono text-[0.8125rem] text-text-2" data-testid="budget">
            {t("ink.lab.budget", {
              used: (budget.total / 1e6).toFixed(1),
              limit: (budget.limit / 1e6).toFixed(0),
              canvases: budget.canvases,
              evictions: budget.evictions,
            })}
          </p>
        </div>
        {manyPages ? (
          <ol className="m-0 flex list-none flex-col gap-4 p-0">
            {manyPages.map((items, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: the list is fixed once mounted
              <li key={i}>
                <InkCanvas
                  label={t("ink.lab.many_label", { n: i + 1, count: MANY_PAGES })}
                  className={cx(
                    "w-full overflow-hidden rounded-tile-sm",
                    surfaceTheme === "paper" ? "bg-paper" : "bg-surface-1",
                  )}
                  style={{ aspectRatio: "820 / 1180" }}
                  items={items}
                  enabled={enabled}
                  tool={tool}
                  color={color}
                  penOnly={penOnly}
                  predict={predict}
                  theme={surfaceTheme}
                  latency={latency}
                  engineRef={(e) => {
                    pageEngines.current[i] = e;
                  }}
                  onError={onError}
                />
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </main>
  );
}
