import type { InkEngine, InkEngineState, InkSelectionPayload, InkToolName } from "@naadi/ink";
import type { InkColor } from "@naadi/shared";
import { Button, cx, ErrorState, Skeleton } from "@naadi/ui";
import { ChevronLeft, ChevronRight, MessageCircleQuestion, PenLine } from "lucide-react";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { askAssistant, useAssistantAvailable } from "../../assistant/ask";
import { ApiError } from "../../auth/apiError";
import { useUiText } from "../../copy/useUiText";
import { InkCanvas } from "../../ink/InkCanvas";
import { StickyInkToolbar } from "../../ink/InkToolbar";
import { InkSaveStatus } from "../../ink/persist/InkSaveStatus";
import { useInkSettings } from "../../ink/persist/settings";
import { useInkLayer } from "../../ink/persist/useInkLayer";
import { requestErrorKey, useIngestFile } from "../api";
import { cropRegion } from "./crop";
import { isFetchFailure, type OpenedPdf, openPdf } from "./pdfjs";

/** Canvases above this many device pixels are rendered at a lower density (iOS caps a canvas at ~16.7 MP). */
const MAX_CANVAS_PIXELS = 12_000_000;
/** Pages within this distance of the viewport are rendered and carry their ink layer; others are boxes. */
const NEAR_MARGIN = "120% 0px";
const THUMB_WIDTH = 84;

export interface PdfViewerProps {
  uploadId: string;
  /** The page to show first (and to jump to when it changes, e.g. a citation link). */
  page: number;
  /** The page most in view changed (the route keeps it in `?page=`). */
  onPageChange?: (page: number) => void;
}

interface Size {
  w: number;
  h: number;
}

function deviceScale(cssW: number, cssH: number): number {
  const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const area = cssW * cssH * dpr * dpr;
  return area > MAX_CANVAS_PIXELS ? Math.sqrt(MAX_CANVAS_PIXELS / (cssW * cssH)) : dpr;
}

/** Start rendering one page into `canvas`, fitted to `cssWidth` CSS px (density capped by MAX_CANVAS_PIXELS). */
async function renderPage(page: PDFPageProxy, canvas: HTMLCanvasElement, cssWidth: number): Promise<RenderTask> {
  const base = page.getViewport({ scale: 1 });
  const scale = cssWidth / base.width;
  const viewport = page.getViewport({ scale });
  const ds = deviceScale(viewport.width, viewport.height);
  canvas.width = Math.floor(viewport.width * ds);
  canvas.height = Math.floor(viewport.height * ds);
  return page.render({
    canvas,
    viewport,
    transform: ds === 1 ? undefined : [ds, 0, 0, ds, 0, 0],
  });
}

function isCancel(e: unknown): boolean {
  return (e as { name?: string } | null)?.name === "RenderingCancelledException";
}

// ── one page ──

interface PageProps {
  doc: PDFDocumentProxy;
  n: number;
  total: number;
  width: number;
  size: Size;
  near: boolean;
  uploadId: string;
  inkOn: boolean;
  tool: InkToolName;
  color: InkColor;
  penOnly: boolean;
  predict: boolean;
  onSize: (n: number, size: Size) => void;
  onEngine: (n: number, engine: InkEngine | null) => void;
  onState: (n: number, state: InkEngineState) => void;
  onRenderFailed: (e: unknown) => void;
  register: (n: number, el: HTMLElement | null) => void;
}

function PageCanvas({
  doc,
  n,
  width,
  onSize,
  onRenderFailed,
  canvasRef,
}: Pick<PageProps, "doc" | "n" | "width" | "onSize" | "onRenderFailed"> & {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
}) {
  const t = useUiText();
  const [rendered, setRendered] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` re-renders the page on Retry
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0) return;
    let task: RenderTask | null = null;
    let alive = true;
    setFailed(false);
    void (async () => {
      try {
        const page: PDFPageProxy = await doc.getPage(n);
        if (!alive) return;
        const base = page.getViewport({ scale: 1 });
        onSize(n, { w: base.width, h: base.height });
        task = await renderPage(page, canvas, width);
        await task.promise;
        if (alive) setRendered(true);
      } catch (e) {
        if (alive && !isCancel(e)) {
          setFailed(true);
          onRenderFailed(e);
        }
      }
    })();
    return () => {
      alive = false;
      task?.cancel();
      // Free the bitmap now: a 50 MB PDF scrolled end to end must not keep every page in memory.
      canvas.width = 0;
      canvas.height = 0;
    };
  }, [doc, n, width, onSize, onRenderFailed, canvasRef, attempt]);
  return (
    <>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={t("library.pdf.page_image", { n: String(n) })}
        className="block h-full w-full"
        data-pdf-canvas={rendered ? "rendered" : failed ? "failed" : "pending"}
      />
      {failed ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-surface-2 p-4 text-center text-text">
          <p className="m-0 text-ui-sm">{t("library.pdf.page_failed")}</p>
          <Button variant="secondary" onClick={() => setAttempt((a) => a + 1)}>
            {t("library.retry")}
          </Button>
        </div>
      ) : rendered ? null : (
        <Skeleton className="absolute inset-0 rounded-none" />
      )}
    </>
  );
}

function PageInk(props: PageProps & { canvasRef: React.RefObject<HTMLCanvasElement | null> }) {
  const { n, uploadId, inkOn, onEngine, onState } = props;
  const t = useUiText();
  const engine = useRef<InkEngine | null>(null);
  const ask = useAssistantAvailable();
  const [selection, setSelection] = useState<InkEngineState["selection"]>(null);
  const [asking, setAsking] = useState(false);
  const surfaceId = `${uploadId}:${n}`;
  const layer = useInkLayer("pdf_page", surfaceId, {
    engineRef: (e: InkEngine | null) => {
      engine.current = e;
      onEngine(n, e);
    },
  });
  const onAsk = useCallback(
    async (payload: InkSelectionPayload) => {
      const canvas = props.canvasRef.current;
      if (!canvas) return;
      setAsking(true);
      try {
        const image = await cropRegion(canvas, payload.rect, payload.png);
        askAssistant({ kind: "explain", context: { type: "pdf_page", id: surfaceId, image } });
        engine.current?.clearSelection();
      } finally {
        setAsking(false);
      }
    },
    [props.canvasRef, surfaceId],
  );
  return (
    <div className="flex flex-col gap-1" data-pdf-ink={surfaceId} data-ink-idle={layer.idle ? "" : undefined}>
      <InkCanvas
        key={surfaceId}
        {...layer.canvasProps}
        enabled={inkOn}
        surfaceType="pdf_page"
        theme="paper"
        tool={props.tool}
        color={props.color}
        penOnly={props.penOnly}
        predict={props.predict}
        onStateChange={(s) => {
          setSelection(s.selection);
          onState(n, s);
        }}
        onAsk={(p) => void onAsk(p)}
        label={t("library.pdf.ink_surface", { n: String(n) })}
        className="relative overflow-hidden rounded-inner bg-white shadow-[0_1px_0_var(--color-line-soft)]"
        style={{ height: Math.round(props.width * (props.size.h / props.size.w)) }}
      >
        <PageCanvas {...props} canvasRef={props.canvasRef} />
      </InkCanvas>
      {ask && selection && inkOn ? (
        <div className="flex justify-end">
          <Button
            variant="assistant"
            icon={<MessageCircleQuestion size={16} />}
            disabled={asking}
            onClick={() => void engine.current?.askAboutSelection()}
            data-pdf-ask=""
          >
            {t("library.pdf.ask")}
          </Button>
        </div>
      ) : null}
      <div className="flex min-h-11 items-center justify-between gap-3 px-1">
        <span className="font-mono text-label-sm text-text-3">
          {t("library.pdf.page_of", { n: String(n), total: String(props.total) })}
        </span>
        <InkSaveStatus layer={layer} />
      </div>
    </div>
  );
}

const PdfPageView = memo(function PdfPageView(props: PageProps) {
  const { n, near, width, size, register } = props;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const height = Math.round(width * (size.h / size.w));
  return (
    <li ref={(el) => register(n, el)} data-page={n} className="scroll-mt-24">
      {near && width > 0 ? (
        <PageInk {...props} canvasRef={canvasRef} />
      ) : (
        <div className="flex flex-col gap-1">
          <div className="rounded-inner bg-surface-2" style={{ height }} />
          <div className="min-h-11" />
        </div>
      )}
    </li>
  );
});

// ── thumbnails ──

function Thumb({
  doc,
  n,
  size,
  current,
  onPick,
  root,
}: {
  doc: PDFDocumentProxy;
  n: number;
  size: Size;
  current: boolean;
  onPick: (n: number) => void;
  root: React.RefObject<HTMLElement | null>;
}) {
  const t = useUiText();
  const ref = useRef<HTMLButtonElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setSeen(true), {
      root: root.current,
      rootMargin: "200px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [seen, root]);
  useEffect(() => {
    const c = canvas.current;
    if (!seen || !c) return;
    let task: RenderTask | null = null;
    let alive = true;
    void doc
      .getPage(n)
      .then(async (page) => {
        if (!alive) return;
        task = await renderPage(page, c, THUMB_WIDTH);
        await task.promise;
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      task?.cancel();
    };
  }, [seen, doc, n]);
  return (
    <li className="shrink-0">
      <button
        ref={ref}
        type="button"
        onClick={() => onPick(n)}
        aria-current={current ? "page" : undefined}
        aria-label={t("library.pdf.go_to_page", { n: String(n) })}
        className={cx(
          "flex flex-col items-center gap-1 rounded-inner p-1.5",
          current ? "bg-paper text-ground" : "text-text-2 hover:bg-surface-2",
        )}
      >
        <span
          aria-hidden="true"
          className="block overflow-hidden rounded-[6px] bg-white"
          style={{ width: THUMB_WIDTH, height: Math.round(THUMB_WIDTH * (size.h / size.w)) }}
        >
          <canvas ref={canvas} className="block h-full w-full" />
        </span>
        <span className="font-mono text-label-sm">{n}</span>
      </button>
    </li>
  );
}

// ── the viewer ──

/**
 * The student's own PDF (segment 15): fetched through a short-lived signed URL (renewed before it expires, and the
 * document reopened if a range request fails after expiry), rendered with pdf.js fitted to the width on iPad and
 * phone, only near the viewport (50 MB files), with page thumbnails and navigation, ink per page
 * (`pdf_page` surfaces `{upload_id}:{page}`) and, while the assistant is available, lasso → "Ask about this".
 */
export function PdfViewer({ uploadId, page, onPageChange }: PdfViewerProps) {
  const t = useUiText();
  const id = useId();
  const file = useIngestFile(uploadId);
  const url = file.data?.url ?? null;
  const urlRef = useRef(url);
  urlRef.current = url;
  const [gen, setGen] = useState(0);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [failed, setFailed] = useState(false);
  const [base, setBase] = useState<Size | null>(null);
  const [sizes, setSizes] = useState<Map<number, Size>>(new Map());
  const [width, setWidth] = useState(0);
  const [near, setNear] = useState<Set<number>>(new Set([page]));
  const [current, setCurrent] = useState(page);
  const [inkOn, setInkOn] = useState(false);
  const { settings, save } = useInkSettings();
  const [tool, setTool] = useState<InkToolName>(settings.tool ?? "pen");
  const [color, setColor] = useState<InkColor>(settings.color ?? "ink");
  const [toolbar, setToolbar] = useState<InkEngineState | null>(null);
  const engines = useRef(new Map<number, InkEngine>());
  const states = useRef(new Map<number, InkEngineState>());
  const column = useRef<HTMLDivElement>(null);
  const strip = useRef<HTMLUListElement>(null);
  const els = useRef(new Map<number, HTMLElement>());
  const io = useRef<IntersectionObserver | null>(null);
  const lastReopen = useRef(0);
  const jumped = useRef<number | null>(null);
  const hasUrl = url !== null;
  const total = doc?.numPages ?? 0;
  const currentRef = useRef(current);
  currentRef.current = current;

  // Open the document (once per upload; again after a failed range request with a fresh URL).
  // biome-ignore lint/correctness/useExhaustiveDependencies: `gen` reopens on purpose; the URL is read from a ref so a renewed URL does not reload the document
  useEffect(() => {
    const u = urlRef.current;
    if (!u) return;
    let alive = true;
    let opened: OpenedPdf | null = null;
    setFailed(false);
    void (async () => {
      try {
        opened = await openPdf(u);
        const d = await opened.task.promise;
        const first = await d.getPage(1);
        const v = first.getViewport({ scale: 1 });
        if (!alive) return;
        setBase({ w: v.width, h: v.height });
        setDoc(d);
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => {
      alive = false;
      void opened?.close();
      setDoc(null);
    };
  }, [uploadId, gen, hasUrl]);

  // Fit to width: the page column's width (rotation, split view, phone).
  useEffect(() => {
    const el = column.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.floor(entry?.contentRect.width ?? 0);
      setWidth((old) => (Math.abs(old - w) >= 2 ? w : old));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Which pages are near the viewport (rendered, with ink) and which one is current.
  useEffect(() => {
    if (!doc) return;
    const observer = new IntersectionObserver(
      (entries) => {
        setNear((old) => {
          const next = new Set(old);
          for (const e of entries) {
            const n = Number((e.target as HTMLElement).dataset.page);
            if (e.isIntersecting) next.add(n);
            else next.delete(n);
          }
          return next;
        });
      },
      { rootMargin: NEAR_MARGIN },
    );
    io.current = observer;
    for (const el of els.current.values()) observer.observe(el);
    return () => {
      observer.disconnect();
      io.current = null;
    };
  }, [doc]);

  useEffect(() => {
    if (!doc) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const mid = window.innerHeight * 0.4;
        let best = current;
        let bestDist = Number.POSITIVE_INFINITY;
        for (const [n, el] of els.current) {
          const r = el.getBoundingClientRect();
          const d = r.top <= mid && r.bottom >= mid ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid));
          if (d < bestDist) {
            bestDist = d;
            best = n;
          }
        }
        setCurrent(best);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [doc, current]);

  useEffect(() => {
    if (doc) onPageChange?.(current);
    const s = states.current.get(current);
    setToolbar(s ?? null);
  }, [current, doc, onPageChange]);

  const goTo = useCallback(
    (n: number, smooth = true) => {
      const target = Math.min(Math.max(1, n), total || 1);
      const el = els.current.get(target);
      setNear((old) => new Set(old).add(target));
      setCurrent(target);
      el?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
    },
    [total],
  );

  // Open at the requested page (first load, and when a citation link changes it).
  useEffect(() => {
    if (!doc || width <= 0 || jumped.current === page) return;
    const first = jumped.current === null;
    jumped.current = page;
    // The route echoing the page in view back as `?page=` is not a jump.
    if (!first && page === currentRef.current) return;
    requestAnimationFrame(() => goTo(page, false));
  }, [doc, width, page, goTo]);

  // Keep the current page's thumbnail in view.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `current` is the trigger; the DOM is read through refs
  useEffect(() => {
    const s = strip.current;
    const btn = s?.querySelector<HTMLElement>(`[aria-current="page"]`);
    if (s && btn) {
      const sr = s.getBoundingClientRect();
      const br = btn.getBoundingClientRect();
      if (br.left < sr.left || br.right > sr.right) s.scrollLeft += br.left - sr.left - 16;
      if (br.top < sr.top || br.bottom > sr.bottom) s.scrollTop += br.top - sr.top - 16;
    }
  }, [current]);

  const register = useCallback((n: number, el: HTMLElement | null) => {
    const old = els.current.get(n);
    if (old && old !== el) io.current?.unobserve(old);
    if (el) {
      els.current.set(n, el);
      io.current?.observe(el);
    } else els.current.delete(n);
  }, []);
  const onSize = useCallback((n: number, size: Size) => {
    setSizes((old) => {
      const prev = old.get(n);
      if (prev && prev.w === size.w && prev.h === size.h) return old;
      return new Map(old).set(n, size);
    });
  }, []);
  const onEngine = useCallback((n: number, engine: InkEngine | null) => {
    if (engine) engines.current.set(n, engine);
    else {
      engines.current.delete(n);
      states.current.delete(n);
    }
  }, []);
  const onState = useCallback((n: number, s: InkEngineState) => {
    states.current.set(n, s);
    if (n === currentRef.current) setToolbar(s);
  }, []);
  const onRenderFailed = useCallback(
    (e: unknown) => {
      // A range request after the signed URL expired: renew the URL and reopen (at most every 30 s). Any other
      // failure stays on its page, which offers a retry.
      if (!isFetchFailure(e) || Date.now() - lastReopen.current < 30_000) return;
      lastReopen.current = Date.now();
      void file.refetch().then(() => setGen((g) => g + 1));
    },
    [file],
  );

  const pages = useMemo(() => Array.from({ length: total }, (_, i) => i + 1), [total]);
  const engine = () => engines.current.get(currentRef.current) ?? null;

  if (file.isError) {
    const gone = file.error instanceof ApiError && file.error.status === 404;
    return (
      <ErrorState
        title={t(gone ? "library.pdf.missing_title" : "library.pdf.load_failed")}
        message={t(gone ? "library.pdf.missing_body" : requestErrorKey(file.error))}
        requestId={file.error instanceof ApiError ? file.error.requestId : null}
        onRetry={gone ? undefined : () => void file.refetch()}
        retrying={file.isFetching}
        retryLabel={t("library.retry")}
      />
    );
  }
  if (failed) {
    return (
      <ErrorState
        title={t("library.pdf.load_failed")}
        message={t("library.pdf.load_failed_body")}
        onRetry={() => void file.refetch().then(() => setGen((g) => g + 1))}
        retrying={file.isFetching}
        retryLabel={t("library.retry")}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4" data-pdf-viewer="" data-pdf-ready={doc && width > 0 ? "" : undefined}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label={t("library.pdf.pages_nav")} className="flex items-center gap-2">
          <Button
            variant="secondary"
            className="min-h-11 px-3"
            aria-label={t("library.pdf.previous")}
            disabled={!doc || current <= 1}
            onClick={() => goTo(current - 1)}
            icon={<ChevronLeft size={18} />}
          />
          <label htmlFor={`${id}-page`} className="sr-only">
            {t("library.pdf.page_input")}
          </label>
          <input
            id={`${id}-page`}
            type="number"
            inputMode="numeric"
            min={1}
            max={Math.max(1, total)}
            value={current}
            disabled={!doc}
            onChange={(e) => {
              const n = Number(e.currentTarget.value);
              if (Number.isInteger(n) && n >= 1 && n <= total) goTo(n);
            }}
            className="min-h-11 w-16 rounded-inner border border-line-control bg-surface-2 px-2 text-center font-mono text-text"
          />
          <span className="font-mono text-text-2 text-ui-sm" aria-live="polite">
            {doc ? t("library.pdf.of_total", { total: String(total) }) : ""}
          </span>
          <Button
            variant="secondary"
            className="min-h-11 px-3"
            aria-label={t("library.pdf.next")}
            disabled={!doc || current >= total}
            onClick={() => goTo(current + 1)}
            icon={<ChevronRight size={18} />}
          />
        </nav>
        <Button
          variant="secondary"
          aria-pressed={inkOn}
          icon={<PenLine size={16} />}
          disabled={!doc}
          onClick={() => setInkOn((v) => !v)}
          className="min-h-11 px-4"
        >
          {inkOn ? t("ink.page.toggle_on") : t("ink.page.toggle")}
        </Button>
      </div>

      <div className="flex flex-col gap-4 laptop:grid laptop:grid-cols-[120px_minmax(0,1fr)] laptop:items-start">
        <ul
          ref={strip}
          aria-label={t("library.pdf.thumbnails")}
          className="m-0 flex list-none gap-1 overflow-x-auto p-0 pb-1 laptop:sticky laptop:top-[calc(var(--shell-top,0px)+16px)] laptop:max-h-[calc(100dvh-var(--shell-top,0px)-32px)] laptop:flex-col laptop:overflow-x-hidden laptop:overflow-y-auto"
        >
          {doc && base
            ? pages.map((n) => (
                <Thumb
                  key={n}
                  doc={doc}
                  n={n}
                  size={sizes.get(n) ?? base}
                  current={n === current}
                  onPick={goTo}
                  root={strip}
                />
              ))
            : null}
        </ul>
        <div ref={column} className="min-w-0">
          {doc && base ? (
            <ol aria-label={t("library.pdf.document")} className="m-0 flex list-none flex-col gap-4 p-0">
              {pages.map((n) => (
                <PdfPageView
                  key={n}
                  doc={doc}
                  n={n}
                  total={total}
                  width={width}
                  size={sizes.get(n) ?? base}
                  near={near.has(n)}
                  uploadId={uploadId}
                  inkOn={inkOn}
                  tool={tool}
                  color={color}
                  penOnly={settings.pen_only}
                  predict={settings.predict}
                  onSize={onSize}
                  onEngine={onEngine}
                  onState={onState}
                  onRenderFailed={onRenderFailed}
                  register={register}
                />
              ))}
            </ol>
          ) : (
            <div aria-busy="true" className="flex flex-col gap-4">
              <p role="status" className="sr-only">
                {t("library.pdf.loading")}
              </p>
              <Skeleton className="aspect-[1/1.3] w-full rounded-inner" />
            </div>
          )}
        </div>
      </div>

      {inkOn && toolbar ? (
        <StickyInkToolbar
          placement="bottom"
          state={toolbar}
          theme="paper"
          onEnabled={setInkOn}
          onTool={(next) => {
            setTool(next);
            for (const e of engines.current.values()) e.setTool(next);
            save({ tool: next }, { debounceMs: 1500 });
          }}
          onColor={(next) => {
            setColor(next);
            for (const e of engines.current.values()) e.setColor(next);
            save({ color: next }, { debounceMs: 1500 });
          }}
          onEraserMode={(mode) => {
            for (const e of engines.current.values()) e.setEraserMode(mode);
          }}
          onUndo={() => engine()?.undo()}
          onRedo={() => engine()?.redo()}
          onClear={() => engine()?.clear()}
        />
      ) : null}
    </div>
  );
}
