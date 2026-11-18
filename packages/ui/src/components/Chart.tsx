import { Table2 } from "lucide-react";
import { type ReactNode, type RefObject, useId, useLayoutEffect, useRef, useState } from "react";
import { cx } from "../cx";

/*
 * Small in-house SVG charts (no chart library) on the design tokens: a grouped BarChart and a LineChart.
 * Marks carry the series colour; every word (axis, legend, tooltip, table) wears text tokens. Hover shows one
 * tooltip with every series at that x. Each chart has its data as a table: always in the DOM for assistive
 * tech (visually hidden), shown on screen with the table toggle. Status is never colour alone: two or more
 * series always get a legend. All wording comes from the caller (ui_copy).
 */

export type ChartValue = string | number | null;

export interface ChartSeries {
  key: string;
  label: string;
}

export interface ChartLabels {
  /** Toggle that shows the data table. */
  showTable: string;
  /** Toggle that hides it again. */
  hideTable: string;
  /** Shown instead of the plot when no row has a number. */
  noData: string;
}

export interface ChartProps {
  /** Accessible name of the chart and the table caption. */
  title: string;
  /** Row key holding the category / x value. */
  x: string;
  /** Column header for the x values in the table. */
  xLabel: string;
  series: ChartSeries[];
  rows: Record<string, ChartValue>[];
  /** Formats a value for axis ticks, tooltip and table (e.g. "72%"). */
  formatValue: (v: number) => string;
  /** Fixed top of the scale (e.g. 100 for percent); otherwise a clean number above the largest value. */
  max?: number;
  labels: ChartLabels;
  className?: string;
}

/** Fixed categorical order (tokens.css --color-series-*): never cycled; series past 8 are not drawn. */
const FILL = [
  "fill-series-1",
  "fill-series-2",
  "fill-series-3",
  "fill-series-4",
  "fill-series-5",
  "fill-series-6",
  "fill-series-7",
  "fill-series-8",
] as const;
const STROKE = [
  "stroke-series-1",
  "stroke-series-2",
  "stroke-series-3",
  "stroke-series-4",
  "stroke-series-5",
  "stroke-series-6",
  "stroke-series-7",
  "stroke-series-8",
] as const;
const BG = [
  "bg-series-1",
  "bg-series-2",
  "bg-series-3",
  "bg-series-4",
  "bg-series-5",
  "bg-series-6",
  "bg-series-7",
  "bg-series-8",
] as const;
export const MAX_SERIES = FILL.length;

const HEIGHT = 220;
const PAD = { top: 12, right: 16, bottom: 32, left: 48 };
const FALLBACK_WIDTH = 480;

function num(v: ChartValue | undefined): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** A clean upper bound (1, 2, 2.5, 5 × 10^n) at or above `v`. */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function ticks(max: number, count = 4): number[] {
  return Array.from({ length: count + 1 }, (_, i) => (max * i) / count);
}

function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const apply = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setWidth(w);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

function clipLabel(text: string, room: number): string {
  // ~7 px per character at the 12 px axis size; long categories end in an ellipsis (full text in the table).
  const fit = Math.max(1, Math.floor(room / 7));
  return text.length <= fit ? text : `${text.slice(0, Math.max(1, fit - 1))}…`;
}

interface Hover {
  index: number;
  left: number;
}

function Legend({ series }: { series: ChartSeries[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-text-2 text-ui-sm">
      {series.map((s, i) => (
        <li key={s.key} className="flex items-center gap-2">
          <span aria-hidden="true" className={cx("h-0.5 w-4 rounded-pill", BG[i])} />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function Tooltip({
  hover,
  category,
  series,
  row,
  formatValue,
}: {
  hover: Hover;
  category: string;
  series: ChartSeries[];
  row: Record<string, ChartValue>;
  formatValue: (v: number) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Positioned through CSSOM (the app's CSP allows no inline style attributes).
  useLayoutEffect(() => {
    ref.current?.style.setProperty("left", `${hover.left}px`);
  }, [hover.left]);
  return (
    <div
      ref={ref}
      aria-hidden="true"
      data-chart-tooltip=""
      className="pointer-events-none absolute top-1 z-10 flex -translate-x-1/2 flex-col gap-1 rounded-inner border border-line-strong bg-surface-2 px-3 py-2 text-ui-sm"
    >
      <span className="text-text-2">{category}</span>
      {series.map((s, i) => {
        const v = num(row[s.key]);
        return (
          <span key={s.key} className="flex items-center gap-2 whitespace-nowrap text-text">
            <span className={cx("h-0.5 w-3 rounded-pill", BG[i])} />
            <strong className="font-semibold">{v === null ? "—" : formatValue(v)}</strong>
            {series.length > 1 ? <span className="text-text-2">{s.label}</span> : null}
          </span>
        );
      })}
    </div>
  );
}

function DataTable({
  id,
  title,
  x,
  xLabel,
  series,
  rows,
  formatValue,
  shown,
}: Omit<ChartProps, "labels" | "className" | "max"> & { id: string; shown: boolean }) {
  return (
    <div id={id} className={shown ? "overflow-x-auto" : "sr-only"}>
      <table className="w-full border-collapse text-left text-ui-sm">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr>
            <th
              scope="col"
              className="border-line border-b py-2 pr-4 font-mono font-medium text-label-sm text-text-3 uppercase"
            >
              {xLabel}
            </th>
            {series.map((s) => (
              <th
                key={s.key}
                scope="col"
                className="border-line border-b py-2 pr-4 font-mono font-medium text-label-sm text-text-3 uppercase"
              >
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
            <tr key={i}>
              <th scope="row" className="border-line-soft border-b py-2 pr-4 text-left font-normal text-text">
                {String(r[x] ?? "")}
              </th>
              {series.map((s) => {
                const v = num(r[s.key]);
                return (
                  <td key={s.key} className="border-line-soft border-b py-2 pr-4 text-text-2 tabular-nums">
                    {v === null ? "—" : formatValue(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Frame({
  props,
  children,
}: {
  props: ChartProps;
  children: (geo: {
    width: number;
    max: number;
    setHover: (h: Hover | null) => void;
    hover: Hover | null;
  }) => ReactNode;
}) {
  const { title, series, rows, labels, className, formatValue, x } = props;
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<Hover | null>(null);
  const [table, setTable] = useState(false);
  const tableId = useId();
  const drawn = series.slice(0, MAX_SERIES);
  const values = rows.flatMap((r) => drawn.map((s) => num(r[s.key])).filter((v): v is number => v !== null));
  const max = props.max ?? niceMax(Math.max(0, ...values));
  const empty = values.length === 0;
  return (
    <figure data-chart="" className={cx("m-0 flex min-w-0 flex-col gap-3", className)}>
      <Legend series={drawn} />
      {empty ? (
        <p className="m-0 text-text-2">{labels.noData}</p>
      ) : (
        <div ref={ref} className="relative w-full" onPointerLeave={() => setHover(null)}>
          <svg
            role="img"
            aria-label={title}
            width={width}
            height={HEIGHT}
            viewBox={`0 0 ${width} ${HEIGHT}`}
            className="block max-w-full overflow-visible"
          >
            {children({ width, max, setHover, hover })}
          </svg>
          {hover && rows[hover.index] ? (
            <Tooltip
              hover={hover}
              category={String(rows[hover.index]?.[x] ?? "")}
              series={drawn}
              row={rows[hover.index] ?? {}}
              formatValue={formatValue}
            />
          ) : null}
        </div>
      )}
      <div>
        <button
          type="button"
          aria-expanded={table}
          aria-controls={tableId}
          onClick={() => setTable((v) => !v)}
          className="inline-flex min-h-11 items-center gap-2 rounded-pill border border-line-control px-4 text-text text-ui-sm hover:bg-surface-2"
        >
          <Table2 aria-hidden="true" size={16} />
          {table ? labels.hideTable : labels.showTable}
        </button>
      </div>
      <DataTable {...props} series={drawn} id={tableId} shown={table} />
    </figure>
  );
}

function Axis({ width, max, formatValue }: { width: number; max: number; formatValue: (v: number) => string }) {
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  return (
    <g>
      {ticks(max).map((t) => {
        const y = PAD.top + plotH - (t / max) * plotH;
        return (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y} y2={y} className="stroke-line" strokeWidth={1} />
            <text x={PAD.left - 8} y={y} dy="0.32em" textAnchor="end" className="fill-text-3 text-[12px]">
              {formatValue(t)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function bandX(width: number, n: number, i: number): { x0: number; band: number } {
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const band = plotW / Math.max(1, n);
  return { x0: PAD.left + i * band, band };
}

/** Bar with a 4 px rounded data end, square at the baseline. */
function barPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export function BarChart(props: ChartProps) {
  const { rows, x, formatValue } = props;
  const series = props.series.slice(0, MAX_SERIES);
  return (
    <Frame props={props}>
      {({ width, max, setHover, hover }) => {
        const plotH = HEIGHT - PAD.top - PAD.bottom;
        const base = PAD.top + plotH;
        return (
          <>
            <Axis width={width} max={max} formatValue={formatValue} />
            {rows.map((r, i) => {
              const { x0, band } = bandX(width, rows.length, i);
              const gap = 2;
              const barW = Math.max(2, Math.min(24, (band * 0.7 - gap * (series.length - 1)) / series.length));
              const groupW = barW * series.length + gap * (series.length - 1);
              const start = x0 + (band - groupW) / 2;
              const label = String(r[x] ?? "");
              return (
                // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                <g key={i} data-bar-group={label}>
                  {hover?.index === i ? (
                    <rect x={x0} y={PAD.top} width={band} height={plotH} className="fill-line-soft" />
                  ) : null}
                  {series.map((s, j) => {
                    const v = num(r[s.key]);
                    if (v === null || v <= 0) return null;
                    const h = (Math.min(v, max) / max) * plotH;
                    return (
                      <path
                        key={s.key}
                        d={barPath(start + j * (barW + gap), base - h, barW, h)}
                        className={FILL[j]}
                        data-series={s.key}
                      />
                    );
                  })}
                  <text x={x0 + band / 2} y={base + 20} textAnchor="middle" className="fill-text-2 text-[12px]">
                    {clipLabel(label, band - 4)}
                  </text>
                  <rect
                    x={x0}
                    y={PAD.top}
                    width={band}
                    height={plotH + PAD.bottom}
                    className="fill-transparent"
                    onPointerEnter={() => setHover({ index: i, left: x0 + band / 2 })}
                    onPointerMove={() => setHover({ index: i, left: x0 + band / 2 })}
                  />
                </g>
              );
            })}
          </>
        );
      }}
    </Frame>
  );
}

export function LineChart(props: ChartProps) {
  const { rows, x, formatValue } = props;
  const series = props.series.slice(0, MAX_SERIES);
  return (
    <Frame props={props}>
      {({ width, max, setHover, hover }) => {
        const plotH = HEIGHT - PAD.top - PAD.bottom;
        const base = PAD.top + plotH;
        const plotW = Math.max(1, width - PAD.left - PAD.right);
        const step = rows.length > 1 ? plotW / (rows.length - 1) : 0;
        const px = (i: number) => (rows.length > 1 ? PAD.left + i * step : PAD.left + plotW / 2);
        const py = (v: number) => base - (Math.min(v, max) / max) * plotH;
        // Every ~nth x label so they never collide.
        const every = Math.max(1, Math.ceil(rows.length / Math.max(1, Math.floor(plotW / 72))));
        return (
          <>
            <Axis width={width} max={max} formatValue={formatValue} />
            {rows.map((r, i) =>
              i % every === 0 || i === rows.length - 1 ? (
                <text
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                  key={i}
                  x={px(i)}
                  y={base + 20}
                  textAnchor="middle"
                  className="fill-text-2 text-[12px]"
                >
                  {clipLabel(String(r[x] ?? ""), Math.max(40, step * every - 4))}
                </text>
              ) : null,
            )}
            {hover ? (
              <line
                x1={px(hover.index)}
                x2={px(hover.index)}
                y1={PAD.top}
                y2={base}
                className="stroke-line-strong"
                strokeWidth={1}
              />
            ) : null}
            {series.map((s, j) => {
              // A null value breaks the line (no invented data between two points).
              let d = "";
              let pen = false;
              rows.forEach((r, i) => {
                const v = num(r[s.key]);
                if (v === null) {
                  pen = false;
                  return;
                }
                d += `${pen ? "L" : "M"}${px(i)},${py(v)}`;
                pen = true;
              });
              return (
                <g key={s.key} data-series={s.key}>
                  <path
                    d={d}
                    fill="none"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    className={STROKE[j]}
                  />
                  {rows.map((r, i) => {
                    const v = num(r[s.key]);
                    if (v === null) return null;
                    const isHover = hover?.index === i;
                    return (
                      <circle
                        // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                        key={i}
                        cx={px(i)}
                        cy={py(v)}
                        r={isHover ? 5 : 4}
                        strokeWidth={2}
                        className={cx(FILL[j], "stroke-surface-1")}
                      />
                    );
                  })}
                </g>
              );
            })}
            {rows.map((_, i) => {
              const left = rows.length > 1 ? px(i) - step / 2 : PAD.left;
              const w = rows.length > 1 ? step : plotW;
              return (
                <rect
                  // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                  key={i}
                  x={Math.max(PAD.left - 8, left)}
                  y={PAD.top}
                  width={Math.max(1, w)}
                  height={plotH}
                  className="fill-transparent"
                  onPointerEnter={() => setHover({ index: i, left: px(i) })}
                  onPointerMove={() => setHover({ index: i, left: px(i) })}
                />
              );
            })}
          </>
        );
      }}
    </Frame>
  );
}
