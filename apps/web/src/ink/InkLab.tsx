import {
  DEFAULT_SIZES,
  type EraserMode,
  type InkEngine,
  type InkEngineState,
  type InkSelectionPayload,
  type InkTheme,
  type InkToolName,
  inputCapabilities,
  LatencyRecorder,
  type SampleStats,
  type SelectionInfo,
} from "@naadi/ink";

import type { InkColor, InkDiff, InkItem } from "@naadi/shared";
import { Button, Chip, cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InkCanvas } from "./InkCanvas";
import { InkToolbar } from "./InkToolbar";
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
      latency: LatencyRecorder;
    };
  }
}

const STRESS_COUNT = 5000;
const STRESS_HEIGHT_SU = 4;

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
      <span className="font-mono text-label-sm text-text-3 uppercase">{checked ? "On" : "Off"}</span>
      {hint ? <span className="sr-only">{hint}</span> : null}
    </button>
  );
}

/** A live frame counter for the 240 fps slow-motion protocol: each video frame shows which rAF it is. */
function FrameCounter() {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let raf = 0;
    let n = 0;
    let prev = performance.now();
    const tick = (ts: number) => {
      n++;
      if (ref.current) ref.current.textContent = `${String(n).padStart(6, "0")} · ${(ts - prev).toFixed(1)} ms`;
      prev = ts;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <p className="m-0 font-mono text-[1.5rem] text-text" aria-live="off">
      <span className="sr-only">Animation frame counter: </span>
      <span ref={ref}>000000 · 0.0 ms</span>
    </p>
  );
}

function fmt(ms: number): string {
  return `${ms.toFixed(1)} ms`;
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

  useEffect(() => {
    window.naadiInkLab = { page: () => pageEngine.current, stress: () => stressEngine.current, latency };
    return () => {
      window.naadiInkLab = undefined;
    };
  }, [latency]);

  // The panel refreshes at most at 4 Hz, and only when a number changed (an idle page does no work);
  // the recorder itself takes every sample.
  useEffect(() => {
    let last = "";
    const id = window.setInterval(() => {
      const summary = latency.summary();
      const stats = { ...(pageEngine.current?.stats ?? {}) };
      const key = JSON.stringify([summary, stats]);
      if (key === last) return;
      last = key;
      setSummary(summary);
      setStats(stats);
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
        `Strokes changed: ${d.added.length} added, ${d.updated.length} updated, ${d.removed.length} removed`,
      ),
    [log],
  );
  const onLasso = useCallback(
    (sel: SelectionInfo, blob: Blob) => {
      log(
        "lasso",
        `Lasso selected ${sel.ids.length} item${sel.ids.length === 1 ? "" : "s"} (PNG ${Math.round(blob.size / 1024)} KB)`,
      );
      showPng(blob, sel);
    },
    [log, showPng],
  );
  const onConvert = useCallback(
    (p: InkSelectionPayload) =>
      log(
        "convert",
        `Convert to text: ${p.ids.length} items, ${Math.round(p.rect.width)}×${Math.round(p.rect.height)} px`,
      ),
    [log],
  );
  const onAsk = useCallback(
    (p: InkSelectionPayload) =>
      log(
        "ask",
        `Circle to ask Naadi: ${p.ids.length} items, ${Math.round(p.rect.width)}×${Math.round(p.rect.height)} px`,
      ),
    [log],
  );
  const onError = useCallback(
    (m: string) => {
      setError(m);
      log("error", m);
    },
    [log],
  );

  const buildStress = () => {
    const items = stressStrokes(STRESS_COUNT, STRESS_HEIGHT_SU);
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

  const sizes = slowMo ? { ...DEFAULT_SIZES, pen: 6 } : DEFAULT_SIZES;
  const surfaceTheme: InkTheme = slowMo ? "paper" : theme;
  const selection = state?.selection ?? null;
  const yes = (b: boolean) => (b ? "Yes" : "No");
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
            System
          </Link>{" "}
          · Ink lab
        </p>
        <h1 className="m-0 font-display text-headline">Ink lab</h1>
        <p className="m-0 max-w-[68ch] text-reading text-text-2">
          Write with Apple Pencil, a stylus, a finger or a mouse. The lab measures input-to-paint latency, shows which
          input features this browser offers, and stress-tests scrolling on a page with 5,000 strokes.
        </p>
        <ul aria-label="Input features" className="m-0 flex list-none flex-wrap gap-2 p-0">
          <li>
            <Chip tone="meta" srPrefix="Coalesced events">{`Coalesced events: ${yes(caps.coalesced)}`}</Chip>
          </li>
          <li>
            <Chip tone="meta" srPrefix="Predicted events">{`Predicted events: ${yes(caps.predicted)}`}</Chip>
          </li>
          <li>
            <Chip tone="meta" srPrefix="Raw pointer updates">{`Raw updates: ${yes(caps.rawUpdate)}`}</Chip>
          </li>
          <li data-testid="pen-status">
            <Chip tone="meta" srPrefix="Pen">{`Pen: ${state?.penPresent ? "Detected" : "Not detected"}`}</Chip>
          </li>
          <li data-testid="finger-status">
            <Chip tone="meta" srPrefix="Finger">{`Finger: ${state?.fingersDraw ? "Draws" : "Scrolls"}`}</Chip>
          </li>
        </ul>
      </header>

      {error ? (
        <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
          {error}
        </p>
      ) : null}

      <div className="sticky top-[calc(var(--shell-top,0px)+8px)] z-10">
        {state ? (
          <InkToolbar
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
      </div>

      <section aria-labelledby="ink-settings-h" className="flex flex-col gap-3">
        <SectionHeading id="ink-settings-h">Pencil and ink</SectionHeading>
        <div className="flex flex-wrap gap-2">
          <Switch
            label="Pen-only drawing"
            checked={penOnly}
            onChange={setPenOnly}
            hint="With a pen detected, fingers scroll and never draw."
          />
          <Switch label="Predicted ink" checked={predict} onChange={setPredict} />
          <Switch
            label="Slow-motion mode"
            checked={slowMo}
            onChange={setSlowMo}
            hint="High contrast and a frame counter for 240 fps video."
          />
          <Button
            variant="secondary"
            aria-pressed={theme === "dark"}
            disabled={slowMo}
            onClick={() => setTheme(theme === "paper" ? "dark" : "paper")}
          >
            {theme === "paper" ? "Paper theme" : "Dark theme"}
          </Button>
        </div>
      </section>

      <section aria-labelledby="ink-page-h" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <SectionHeading id="ink-page-h">Page</SectionHeading>
          <span className="font-mono text-label-sm text-text-3 uppercase" data-testid="page-count">
            {`${state?.count ?? 0} item${state?.count === 1 ? "" : "s"}`}
          </span>
        </div>
        {slowMo ? <FrameCounter /> : null}
        <div className="relative">
          <InkCanvas
            label="Ink page"
            className={cx(
              "w-full overflow-hidden rounded-tile-sm",
              surfaceTheme === "paper" ? "ink-paper-dotted" : "ink-dark-dotted",
            )}
            style={{ aspectRatio: "820 / 1180" }}
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
          />
          {selection && enabled && tool === "lasso" ? (
            <div
              role="toolbar"
              aria-label="Selection"
              className="absolute z-20 flex flex-wrap gap-1 rounded-pill border border-line-strong bg-ground p-1.5"
              style={{
                left: `clamp(8px, ${selection.rect.left}px, calc(100% - 340px))`,
                top: `${selection.rect.top + selection.rect.height + 12}px`,
              }}
            >
              <span className="sr-only">Drag the selection to move it.</span>
              <Button variant="secondary" className="min-h-11" onClick={() => pageEngine.current?.duplicateSelection()}>
                Copy
              </Button>
              <Button variant="secondary" className="min-h-11" onClick={() => pageEngine.current?.deleteSelection()}>
                Delete
              </Button>
              <Button
                variant="secondary"
                className="min-h-11"
                onClick={() => void pageEngine.current?.convertSelection()}
              >
                Convert to text
              </Button>
              <Button
                variant="assistant"
                className="min-h-11"
                onClick={() => void pageEngine.current?.askAboutSelection()}
              >
                Ask Naadi
              </Button>
            </div>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="ink-latency-h" className="flex flex-col gap-3" data-testid="latency-panel">
        <SectionHeading id="ink-latency-h">Latency</SectionHeading>
        <div className="grid grid-cols-1 gap-3 tablet:grid-cols-2">
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 rounded-block border border-line-soft bg-surface-1 p-4">
            <dt className="text-text-3">Input to paint p50</dt>
            <dd className="m-0 font-mono" data-metric="paint-p50">
              {fmt(paintSummary.p50)}
            </dd>
            <dt className="text-text-3">Input to paint p95</dt>
            <dd className="m-0 font-mono" data-metric="paint-p95">
              {fmt(paintSummary.p95)}
            </dd>
            <dt className="text-text-3">Input to next frame p50</dt>
            <dd className="m-0 font-mono" data-metric="frame-p50">
              {fmt(frameSummary.p50)}
            </dd>
            <dt className="text-text-3">Input to next frame p95</dt>
            <dd className="m-0 font-mono" data-metric="frame-p95">
              {fmt(frameSummary.p95)}
            </dd>
            <dt className="text-text-3">Painted frames</dt>
            <dd className="m-0 font-mono" data-metric="frames">
              {paintSummary.count}
            </dd>
          </dl>
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 rounded-block border border-line-soft bg-surface-1 p-4">
            <dt className="text-text-3">Samples used</dt>
            <dd className="m-0 font-mono" data-metric="samples">
              {stats.accepted ?? 0}
            </dd>
            <dt className="text-text-3">Coalesced batches</dt>
            <dd className="m-0 font-mono" data-metric="coalesced">
              {stats.coalescedBatches ?? 0}
            </dd>
            <dt className="text-text-3">Predicted samples</dt>
            <dd className="m-0 font-mono" data-metric="predicted">
              {stats.predicted ?? 0}
            </dd>
            <dt className="text-text-3">Coalesced id mismatches</dt>
            <dd className="m-0 font-mono" data-metric="id-mismatch">
              {stats.idMismatch ?? 0}
            </dd>
            <dt className="text-text-3">Dropped samples</dt>
            <dd className="m-0 font-mono" data-metric="dropped">
              {stats.dropped ?? 0}
            </dd>
          </dl>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => latency.reset()}>
            Reset measurements
          </Button>
        </div>
        <details className="rounded-block border border-line-soft bg-surface-1 p-4 text-text-2">
          <summary className="min-h-11 cursor-pointer content-center font-semibold text-text">
            240 fps slow-motion protocol (real iPad)
          </summary>
          <ol className="mt-2 mb-0 flex flex-col gap-1.5 pl-5">
            <li>Turn on Slow-motion mode: thick black ink on paper and a frame counter above the page.</li>
            <li>Record the iPad screen with another phone at 240 fps, close up, Pencil tip and ink both in frame.</li>
            <li>Draw fast, short strokes. In the video, count frames from the tip touching to the first ink pixel.</li>
            <li>Each frame is 4.17 ms; the target is 20 ms or less (about 5 frames). Note the median of 10 strokes.</li>
            <li>Repeat with Predicted ink off. Record both in docs/PROGRESS.md with the iPad and iPadOS version.</li>
          </ol>
        </details>
      </section>

      <section aria-labelledby="ink-events-h" className="flex flex-col gap-3">
        <SectionHeading id="ink-events-h">Events</SectionHeading>
        <div className="grid grid-cols-1 gap-3 tablet:grid-cols-[1fr_auto]">
          {events.length === 0 ? (
            <p className="m-0 text-text-2">No ink events yet. Draw on the page.</p>
          ) : (
            <ol aria-label="Recent ink events" className="m-0 flex list-none flex-col gap-1.5 p-0" data-testid="events">
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
                alt="Lasso selection as sent to later features"
                className="max-h-40 max-w-60 rounded-inner border border-line-strong object-contain"
              />
              <figcaption className="font-mono text-label-sm text-text-3 uppercase">{`${png.w}×${png.h} px crop`}</figcaption>
            </figure>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="ink-stress-h" className="flex flex-col gap-3" data-testid="stress">
        <SectionHeading id="ink-stress-h">Scroll stress test</SectionHeading>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={buildStress}>
            {stressItems ? "Rebuild 5,000-stroke page" : "Build 5,000-stroke page"}
          </Button>
          <Button variant="secondary" disabled={!stressItems || measuring} onClick={() => void runScroll()}>
            {measuring ? "Measuring scroll…" : "Measure scroll"}
          </Button>
          {scroll ? (
            <p className="m-0 font-mono text-[0.8125rem] text-text-2" data-testid="scroll-result" data-fps={scroll.fps}>
              {`${scroll.fps} fps · p95 frame ${scroll.p95FrameMs} ms · ${scroll.longFrames} long frames of ${scroll.frames}`}
            </p>
          ) : null}
        </div>
        {stressItems ? (
          <div ref={stressRef}>
            <InkCanvas
              label="Stress page with 5,000 strokes"
              className={cx(
                "w-full overflow-hidden rounded-tile-sm",
                surfaceTheme === "paper" ? "bg-paper" : "bg-surface-1",
              )}
              style={{ aspectRatio: `1 / ${STRESS_HEIGHT_SU}` }}
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
              {`${stressCount.toLocaleString("en")} items`}
            </p>
          </div>
        ) : null}
      </section>
    </main>
  );
}
