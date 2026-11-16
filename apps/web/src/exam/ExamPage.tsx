import type { AttemptIn, BlockClock, BlockDetail, QuestionView, SectionLayout } from "@naadi/shared";
import { Button, cx, ErrorState, OfflineBanner, Skeleton } from "@naadi/ui";
import { Link, Navigate, useNavigate } from "@tanstack/react-router";
import {
  Calculator as CalcIcon,
  Contrast,
  Flag,
  FlaskConical,
  Grid3x3,
  Highlighter,
  PenLine,
  Strikethrough,
  Wrench,
  X,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { SafeHtml } from "../content/SafeHtml";
import { Sheet } from "../learn/Sheet";
import {
  isUnreachable,
  useBlock,
  useBlockQuestion,
  useFlag,
  usePrefetchQuestion,
  useSendAttempt,
  useUserId,
} from "../practice/api";
import { useCopy } from "../practice/copy";
import { isTypingTarget, keyAction } from "../practice/keys";
import { LabValuesSheet } from "../practice/LabValuesSheet";
import { enqueueAttempt, flushQueue, queuedFor } from "../practice/outbox";
import { type SectionAction, type ServerClock, useFinishBlock, useSectionAction, useServerClock } from "./api";
import { Calculator } from "./Calculator";
import { crossedThreshold, formatRemaining, remainingMs, spokenParts } from "./clock";
import "./exam.css";
import { paint, type Span, selectionSpan, toggleSpan } from "./highlight";
import { markFinished, readMarks, sweep, writeMarks } from "./local";
import { Navigator } from "./Navigator";
import { ScratchSheet } from "./ScratchSheet";
import { useExamChrome } from "./useExamChrome";

type T = ReturnType<typeof useCopy>;

/** The exam covers the app shell (Exam board: no navigation), scrolling on its own. */
const FRAME = "fixed inset-0 z-30 overflow-y-auto overscroll-contain bg-ground text-text";

// ── Time ──

/** Re-render on a steady tick (the display is recomputed from the deadline each time, never decremented). */
function useNow(ms = 100): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** Time left in the section on the server's clock (frozen while paused). */
export function sectionLeft(clock: BlockClock, offsetMs: number, now: number): number {
  if (clock.state === "paused" || !clock.deadline_at) return Math.max(0, clock.remaining_ms ?? 0);
  return remainingMs(clock.deadline_at, offsetMs, now);
}

/** Break time left: the pool at `server_now`, minus what has run since on the server's clock. */
export function breakLeft(clock: BlockClock, offsetMs: number, now: number): number {
  const left = clock.break_remaining_ms ?? clock.break_left_ms;
  if (clock.state !== "break") return Math.max(0, left);
  return Math.max(0, Date.parse(clock.server_now) + left - (now + offsetMs));
}

function spoken(t: T, ms: number): string {
  const p = spokenParts(ms);
  return "minutes" in p
    ? t("exam.time_left_minutes", { minutes: p.minutes })
    : t("exam.time_left_seconds", { seconds: p.seconds });
}

/** When the displayed time reaches 0, ask the server what happened (section ended, break over) once. */
function useResyncAtZero(left: number, server: ServerClock, key: string) {
  const fired = useRef<string | null>(null);
  useEffect(() => {
    if (left > 0 || fired.current === key) return;
    fired.current = key;
    // the server ends the section at its own deadline: give it a beat past the deadline
    const id = window.setTimeout(() => void server.resync(), 400);
    return () => window.clearTimeout(id);
  }, [left, server, key]);
}

function errorText(t: T, err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === null) return t("exam.error.offline");
    if (err.code && err.status === 409) return t(`exam.error.${err.code}`);
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

// ── Chrome ──

function TimerPill({ left, paused, t }: { left: number; paused: boolean; t: T }) {
  const low = left <= 5 * 60_000;
  return (
    <span
      role="timer"
      aria-label={`${paused ? t("exam.paused") : t("exam.time_left")}: ${spoken(t, left)}`}
      className={cx(
        "inline-flex min-h-11 items-center gap-2 rounded-pill px-4 font-mono text-[0.9375rem] tabular-nums",
        low ? "border-2 border-amber bg-transparent text-amber" : "bg-paper text-ground",
      )}
    >
      <span aria-hidden="true">{formatRemaining(left)}</span>
      {low ? <span className="font-sans text-[0.75rem] uppercase tracking-[0.08em]">{t("exam.low_time")}</span> : null}
    </span>
  );
}

function Header({
  t,
  block,
  clock,
  layout,
  posInSection,
  left,
  breakMs,
  onPause,
  onEnd,
  pausing,
  children,
}: {
  t: T;
  block: BlockDetail;
  clock: BlockClock;
  layout: SectionLayout;
  posInSection: number;
  left: number;
  breakMs: number;
  onPause: () => void;
  onEnd: () => void;
  pausing: boolean;
  children?: ReactNode;
}) {
  const pausable = clock.pausable;
  const multi = clock.sections > 1;
  return (
    <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-2 border-line-soft border-b bg-ground/95 px-4 pt-[max(10px,env(safe-area-inset-top))] pb-2.5 backdrop-blur-[12px] tablet:px-6">
      <Link
        to="/practice"
        aria-label={t("exam.leave")}
        className="flex size-11 shrink-0 items-center justify-center rounded-full border border-line bg-surface-1 text-text no-underline tablet:hidden"
      >
        <X aria-hidden="true" size={16} />
      </Link>
      <span className="hidden font-bold font-display text-[1.125rem] tablet:inline">
        {t(block.config.mode === "exam" ? "exam.title_exam" : "exam.title_timed")}
      </span>
      <span className="font-mono text-[0.8125rem] text-text-2">
        <span className="tablet:hidden">{t("exam.position_short", { n: posInSection, total: layout.count })}</span>
        <span className="hidden tablet:inline">
          {multi
            ? t("exam.position", { block: layout.n, blocks: clock.sections, n: posInSection, total: layout.count })
            : t("exam.position_single", { n: posInSection, total: layout.count })}
        </span>
      </span>
      <span className="flex-[1_1_8px]" />
      {multi ? (
        <span className="hidden font-mono text-[0.8125rem] text-text-2 tablet:inline">
          {t("exam.break_left", { time: formatRemaining(breakMs) })}
        </span>
      ) : null}
      <TimerPill left={left} paused={clock.state === "paused"} t={t} />
      {pausable ? (
        <Button variant="secondary" onClick={onPause} disabled={pausing} className="min-h-11 px-4">
          {t("exam.pause")}
        </Button>
      ) : (
        <span className="hidden min-h-11 items-center rounded-pill border border-line-dashed border-dashed px-4 text-[0.8125rem] text-text-muted laptop:inline-flex">
          {t("exam.pause_off")}
        </span>
      )}
      <Button variant="secondary" onClick={onEnd} className="min-h-11 px-4">
        {t("exam.end_block")}
      </Button>
      {children}
    </header>
  );
}

// ── Screens around the questions ──

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex min-h-[70dvh] w-full max-w-[44rem] flex-col justify-center gap-6 px-5 py-10">
      {children}
    </div>
  );
}

function Rules({ t, block, clock }: { t: T; block: BlockDetail; clock: BlockClock }) {
  const sections = block.config.sections ?? [];
  const first = sections[0];
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0 text-text-2">
      <li>
        {t(clock.sections > 1 ? "exam.rule_blocks" : "exam.rule_block", {
          blocks: clock.sections,
          questions: first?.count ?? block.total,
          minutes: Math.round((first?.time_limit_s ?? 0) / 60),
        })}
      </li>
      {clock.sections > 1 ? (
        <li>{t("exam.rule_break", { minutes: Math.round((block.config.break_s ?? 0) / 60) })}</li>
      ) : null}
      <li>{t(clock.pausable ? "exam.rule_pause_timed" : "exam.rule_pause_exam")}</li>
      <li>{t("exam.rule_no_going_back")}</li>
      <li>{t(block.config.mode === "exam" ? "exam.rule_silent" : "exam.rule_review")}</li>
    </ul>
  );
}

function StartScreen({
  t,
  block,
  clock,
  onStart,
  busy,
  error,
}: {
  t: T;
  block: BlockDetail;
  clock: BlockClock;
  onStart: () => void;
  busy: boolean;
  error: string | null;
}) {
  return (
    <Centered>
      <span className="font-mono text-label text-text-3 uppercase">
        {block.config.mock
          ? t(`exam.mock.${block.config.mock}`)
          : t(block.config.mode === "exam" ? "exam.title_exam" : "exam.title_timed")}
      </span>
      <h1 className="m-0 font-bold font-display text-headline">{t("exam.ready_title")}</h1>
      <Rules t={t} block={block} clock={clock} />
      {error ? (
        <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" size="cta" onClick={onStart} disabled={busy} className="px-8">
          {clock.sections > 1 ? t("exam.start_block", { n: 1 }) : t("exam.start")}
        </Button>
        <Link to="/practice" className="text-text-2">
          {t("exam.not_now")}
        </Link>
      </div>
    </Centered>
  );
}

function BreakScreen({
  t,
  clock,
  breakMs,
  onStart,
  busy,
  error,
  done,
}: {
  t: T;
  clock: BlockClock;
  breakMs: number;
  onStart: () => void;
  busy: boolean;
  error: string | null;
  done: number;
}) {
  const next = clock.section_n ?? done + 1;
  return (
    <Centered>
      <span className="font-mono text-label text-text-3 uppercase">{t("exam.break_label")}</span>
      <h1 className="m-0 font-bold font-display text-headline">
        {t("exam.break_title", { done: next - 1, blocks: clock.sections })}
      </h1>
      <p className="m-0 flex items-baseline gap-3">
        <span
          role="timer"
          aria-label={`${t("exam.break_time_left")}: ${spoken(t, breakMs)}`}
          className="font-bold font-display text-[clamp(3rem,2rem+4vw,4.5rem)] tabular-nums leading-none"
        >
          <span aria-hidden="true">{formatRemaining(breakMs)}</span>
        </span>
        <span className="text-text-2">{t("exam.break_time_left")}</span>
      </p>
      <p className="m-0 text-text-2">{t("exam.break_auto", { n: next })}</p>
      {error ? (
        <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
          {error}
        </p>
      ) : null}
      <div>
        <Button variant="primary" size="cta" onClick={onStart} disabled={busy} className="px-8">
          {t("exam.start_block", { n: next })}
        </Button>
      </div>
    </Centered>
  );
}

function PausedScreen({ t, left, onResume, busy }: { t: T; left: number; onResume: () => void; busy: boolean }) {
  return (
    <Centered>
      <span className="font-mono text-label text-text-3 uppercase">{t("exam.paused")}</span>
      <h1 className="m-0 font-bold font-display text-headline">{t("exam.paused_title")}</h1>
      <p className="m-0 text-text-2">{t("exam.paused_body", { time: formatRemaining(left) })}</p>
      <div>
        <Button variant="primary" size="cta" onClick={onResume} disabled={busy} className="px-8">
          {t("exam.resume")}
        </Button>
      </div>
    </Centered>
  );
}

function EndDialog({
  t,
  open,
  onClose,
  onConfirm,
  last,
  unanswered,
  busy,
}: {
  t: T;
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  last: boolean;
  unanswered: number;
  busy: boolean;
}) {
  return (
    <Sheet open={open} onClose={onClose} title={t(last ? "exam.end_title_last" : "exam.end_title")}>
      <div className="flex flex-col gap-4 pb-2">
        <p className="m-0 text-text-2">{t(last ? "exam.end_body_last" : "exam.end_body")}</p>
        {unanswered > 0 ? (
          <p className="m-0 font-semibold text-amber">{t("exam.end_unanswered", { count: unanswered })}</p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t("exam.keep_going")}
          </Button>
          <Button variant="strong" onClick={onConfirm} disabled={busy}>
            {t(last ? "exam.end_confirm_last" : "exam.end_confirm")}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}

// ── Zoom and contrast (device setting) ──

const VIEW_KEY = "naadi.exam.view.v1";
interface ViewPrefs {
  zoom: 1 | 2 | 3 | 4;
  contrast: "normal" | "high";
}

function readPrefs(): ViewPrefs {
  try {
    const p = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "{}") as Partial<ViewPrefs>;
    const zoom = p.zoom && [1, 2, 3, 4].includes(p.zoom) ? p.zoom : 2;
    return { zoom, contrast: p.contrast === "high" ? "high" : "normal" };
  } catch {
    return { zoom: 2, contrast: "normal" };
  }
}

function ViewSheet({
  t,
  open,
  onClose,
  prefs,
  onChange,
}: {
  t: T;
  open: boolean;
  onClose: () => void;
  prefs: ViewPrefs;
  onChange: (p: ViewPrefs) => void;
}) {
  const chip = (on: boolean) =>
    cx(
      "inline-flex min-h-11 items-center rounded-pill border px-4 text-[0.9375rem]",
      on ? "border-paper bg-paper text-ground" : "border-line-control bg-transparent text-text",
    );
  return (
    <Sheet open={open} onClose={onClose} title={t("exam.tool.view")}>
      <div className="flex flex-col gap-5 pb-2">
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="pb-2 font-mono text-label text-text-3 uppercase">{t("exam.view.text_size")}</legend>
          <div className="flex flex-wrap gap-2">
            {([1, 2, 3, 4] as const).map((z) => (
              <button
                key={z}
                type="button"
                aria-pressed={prefs.zoom === z}
                className={chip(prefs.zoom === z)}
                onClick={() => onChange({ ...prefs, zoom: z })}
              >
                {t(`exam.view.size_${z}`)}
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="pb-2 font-mono text-label text-text-3 uppercase">{t("exam.view.contrast")}</legend>
          <div className="flex flex-wrap gap-2">
            {(["normal", "high"] as const).map((c) => (
              <button
                key={c}
                type="button"
                aria-pressed={prefs.contrast === c}
                className={chip(prefs.contrast === c)}
                onClick={() => onChange({ ...prefs, contrast: c })}
              >
                {t(`exam.view.contrast_${c}`)}
              </button>
            ))}
          </div>
        </fieldset>
      </div>
    </Sheet>
  );
}

// ── The question ──

const OPTION =
  "exam-option flex w-full min-h-[60px] items-center gap-3.5 rounded-block border p-2 pr-4 text-left transition-colors duration-(--duration-control) ease-out box-border";
const BADGE = "flex size-11 shrink-0 items-center justify-center rounded-full font-bold";

type Tool = "labs" | "calc" | "scratch" | "view" | "tools" | "questions" | null;

interface RunningProps {
  t: T;
  blockId: string;
  n: number;
  block: BlockDetail;
  clock: BlockClock;
  layout: SectionLayout;
  server: ServerClock;
  offsetMs: number;
  now: number;
}

function Running({ t, blockId, n, block, clock, layout, server, offsetMs, now }: RunningProps) {
  const nav = useNavigate();
  const userId = useUserId();
  const q = useBlockQuestion(blockId, n);
  const attempts = useSendAttempt();
  const flag = useFlag(blockId);
  const prefetch = usePrefetchQuestion();
  const action = useSectionAction(blockId);
  const finish = useFinishBlock(blockId);
  const stemRef = useRef<HTMLDivElement>(null);
  const shownAt = useRef(Date.now());
  const headingRef = useRef<HTMLHeadingElement>(null);

  const firstN = layout.first_index + 1;
  const lastN = layout.first_index + layout.count;
  const pos = n - firstN + 1;
  const sectionQs = block.questions.filter((x) => x.n >= firstN && x.n <= lastN);
  const view: QuestionView | undefined = q.data?.data;
  const qid = view?.question.id;

  const [chosen, setChosen] = useState<string | null>(null);
  const [queued, setQueued] = useState<Set<number>>(() => new Set());
  const [sendError, setSendError] = useState<string | null>(null);
  const [spans, setSpans] = useState<Span[]>([]);
  const [struck, setStruck] = useState<string[]>([]);
  const [strikeMode, setStrikeMode] = useState(false);
  const [tool, setTool] = useState<Tool>(null);
  const [ending, setEnding] = useState(false);
  const [prefs, setPrefs] = useState<ViewPrefs>(readPrefs);
  const [live, setLive] = useState("");
  const prevLeft = useRef<number | null>(null);

  const left = sectionLeft(clock, offsetMs, now);
  const breakMs = breakLeft(clock, offsetMs, now);
  useResyncAtZero(left, server, `${clock.section_n}:${clock.deadline_at}`);

  // 5- and 1-minute warnings, once each, for screen readers.
  useEffect(() => {
    const crossed = crossedThreshold(prevLeft.current, left);
    prevLeft.current = left;
    if (crossed !== null) setLive(t(crossed === 60_000 ? "exam.announce_1" : "exam.announce_5"));
  }, [left, t]);

  // The choice shown: the server's, or one queued on this device while offline.
  useEffect(() => {
    if (!view || !userId) return;
    const pending = queuedFor(userId, blockId, view.question.id);
    setChosen(pending?.body.chosen ?? view.attempt?.chosen ?? null);
  }, [view, userId, blockId]);

  useEffect(() => {
    shownAt.current = Date.now();
    headingRef.current?.focus({ preventScroll: true });
    if (n < lastN) prefetch(blockId, n + 1);
  }, [blockId, n, lastN, prefetch]);

  // Device-local marks for this question.
  useEffect(() => {
    if (!userId) return;
    let alive = true;
    setSpans([]);
    setStruck([]);
    void readMarks(userId, blockId, n).then((m) => {
      if (!alive || !m) return;
      setSpans(m.highlights);
      setStruck(m.struck);
    });
    return () => {
      alive = false;
    };
  }, [userId, blockId, n]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: repaint when the stem's text or the zoom changes the layout
  useEffect(() => {
    paint(stemRef.current, spans);
    return () => {
      paint(null, []);
    };
  }, [spans, view?.question.stem_html, prefs.zoom]);

  const saveMarks = useCallback(
    (h: Span[], s: string[]) => {
      if (userId) void writeMarks(userId, blockId, n, { highlights: h, struck: s });
    },
    [userId, blockId, n],
  );

  // Answers saved while offline go out as soon as the connection returns.
  useEffect(() => {
    if (!userId) return;
    const run = () =>
      void flushQueue(userId, attempts.send, isUnreachable, (b, out) => attempts.applied(b, out)).then(() =>
        setQueued(new Set()),
      );
    run();
    window.addEventListener("online", run);
    return () => window.removeEventListener("online", run);
  }, [userId, attempts]);

  const choose = useCallback(
    async (label: string) => {
      if (!view || !userId || clock.state !== "running") return;
      if (struck.includes(label)) {
        const s = struck.filter((x) => x !== label);
        setStruck(s);
        saveMarks(spans, s);
      }
      setChosen(label);
      setSendError(null);
      const body: AttemptIn = {
        question_id: view.question.id,
        chosen: label,
        seconds: (view.attempt?.seconds ?? 0) + Math.max(0, Math.round((Date.now() - shownAt.current) / 1000)),
        flagged: view.flagged,
      };
      try {
        const out = await attempts.send(blockId, body);
        attempts.applied(blockId, out);
        server.adopt(out.clock);
      } catch (err) {
        if (isUnreachable(err)) {
          enqueueAttempt(userId, blockId, body);
          setQueued((s) => new Set(s).add(n));
        } else {
          setSendError(errorText(t, err));
          setChosen(view.attempt?.chosen ?? null);
          if (err instanceof ApiError && err.status === 409) void server.resync();
        }
      }
    },
    [view, userId, clock.state, struck, spans, saveMarks, attempts, blockId, server, n, t],
  );

  const toggleStrike = (label: string) => {
    const s = struck.includes(label) ? struck.filter((x) => x !== label) : [...struck, label];
    setStruck(s);
    saveMarks(spans, s);
  };

  const highlightSelection = () => {
    const root = stemRef.current;
    if (!root) return;
    const span = selectionSpan(root, window.getSelection());
    if (!span) {
      setLive(t("exam.highlight_hint"));
      return;
    }
    const h = toggleSpan(spans, span);
    setSpans(h);
    saveMarks(h, struck);
    window.getSelection()?.removeAllRanges();
  };

  const clearHighlights = () => {
    setSpans([]);
    saveMarks([], struck);
  };

  const toggleFlag = useCallback(() => {
    if (!qid || !view) return;
    flag.mutate({ questionId: qid, flagged: !view.flagged }, { onError: (e) => setSendError(errorText(t, e)) });
  }, [flag, qid, view, t]);

  const go = useCallback(
    (to: number) => {
      if (to >= firstN && to <= lastN)
        void nav({ to: "/practice/blocks/$blockId/$n", params: { blockId, n: to }, replace: true });
    },
    [nav, blockId, firstN, lastN],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target) || tool !== null || ending || !view) return;
      const a = keyAction(e, view.question.options.length);
      if (!a) return;
      if (a.type === "choose") {
        const o = view.question.options[a.index];
        if (o) void choose(o.label);
      } else if (a.type === "flag") toggleFlag();
      else if (a.type === "next" || a.type === "submit") go(n + 1);
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, choose, toggleFlag, go, n, tool, ending]);

  const transition = (a: SectionAction) =>
    action.mutate(
      { n: layout.n, action: a },
      {
        onSuccess: (b) => {
          server.adopt(b.clock);
          void server.resync();
          setEnding(false);
        },
        onError: (e) => {
          setSendError(errorText(t, e));
          void server.resync();
        },
      },
    );

  const lastSection = layout.n >= clock.sections;
  const unanswered = sectionQs.filter((x) => !x.answered && !queued.has(x.n)).length;
  const viewAttrs = { "data-exam-zoom": String(prefs.zoom), "data-exam-contrast": prefs.contrast };
  const setView = (p: ViewPrefs) => {
    setPrefs(p);
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(p));
    } catch {
      // device setting only
    }
  };

  const toolButton = (
    key: string,
    icon: ReactNode,
    label: string,
    onClick: () => void,
    pressed?: boolean,
    after?: () => void,
  ) => (
    <button
      key={key}
      type="button"
      aria-pressed={pressed}
      onClick={() => {
        onClick();
        after?.();
      }}
      // keep a text selection alive while tapping "Highlight" on iPad
      onPointerDown={key === "highlight" ? (e) => e.preventDefault() : undefined}
      className={cx(
        "flex min-h-[52px] w-full items-center gap-3 rounded-[18px] border px-4 text-left text-[0.9375rem]",
        pressed ? "border-paper bg-paper text-ground" : "border-line bg-surface-1 text-text hover:border-line-control",
      )}
    >
      {icon}
      <span className="min-w-0 flex-1">{label}</span>
    </button>
  );

  // In the phone's tools sheet, a tool that acts on the question closes the sheet (`after`).
  const makeTools = (after?: () => void) => [
    toolButton(
      "flag",
      <Flag aria-hidden="true" size={18} fill={view?.flagged ? "currentColor" : "none"} />,
      view?.flagged ? t("exam.tool.unflag") : t("exam.tool.flag"),
      toggleFlag,
      view?.flagged ?? false,
      after,
    ),
    toolButton(
      "highlight",
      <Highlighter aria-hidden="true" size={18} />,
      t("exam.tool.highlight"),
      highlightSelection,
      undefined,
      after,
    ),
    toolButton(
      "strike",
      <Strikethrough aria-hidden="true" size={18} />,
      t("exam.tool.strike"),
      () => setStrikeMode((v) => !v),
      strikeMode,
      after,
    ),
    toolButton("labs", <FlaskConical aria-hidden="true" size={18} />, t("exam.tool.labs"), () => setTool("labs")),
    toolButton("calc", <CalcIcon aria-hidden="true" size={18} />, t("exam.tool.calculator"), () => setTool("calc")),
    toolButton("scratch", <PenLine aria-hidden="true" size={18} />, t("exam.tool.scratch"), () => setTool("scratch")),
    toolButton("view", <Contrast aria-hidden="true" size={18} />, t("exam.tool.view"), () => setTool("view")),
  ];
  const tools = makeTools();

  const header = (
    <Header
      t={t}
      block={block}
      clock={clock}
      layout={layout}
      posInSection={pos}
      left={left}
      breakMs={breakMs}
      onPause={() => transition("pause")}
      onEnd={() => setEnding(true)}
      pausing={action.isPending}
    />
  );

  let body: ReactNode;
  if (q.isPending && !q.data) {
    body = (
      <div aria-busy="true" className="flex flex-col gap-5">
        <p role="status" className="sr-only">
          {t("exam.loading_question")}
        </p>
        <Skeleton className="h-40 rounded-block" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-[60px] rounded-block" />
        ))}
      </div>
    );
  } else if (!view) {
    body = (
      <ErrorState
        title={t("exam.question_error_title")}
        message={errorText(t, q.error)}
        requestId={q.error instanceof ApiError ? q.error.requestId : null}
        onRetry={() => {
          void server.resync();
          void q.refetch();
        }}
        retrying={q.isFetching}
      />
    );
  } else {
    body = (
      <div className="flex flex-col gap-6">
        <h2 ref={headingRef} tabIndex={-1} className="sr-only">
          {t("exam.question_heading", { n: pos, total: layout.count })}
        </h2>
        <div ref={stemRef} className="exam-reading rounded-block [&_p]:m-0 [&_p+p]:mt-4 [&_table]:my-3">
          <SafeHtml html={view.question.stem_html} />
        </div>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="sr-only">{t("exam.options_legend")}</legend>
          {strikeMode ? (
            <p role="status" className="m-0 pb-1 text-text-2 text-ui-sm">
              {t("exam.strike_hint")}
            </p>
          ) : null}
          {view.question.options.map((o, i) => {
            const on = chosen === o.label;
            const off = struck.includes(o.label);
            const words = [o.label, on ? t("exam.option_chosen") : null, off ? t("exam.option_struck") : null].filter(
              Boolean,
            );
            return (
              <button
                key={o.label}
                type="button"
                aria-pressed={on}
                aria-keyshortcuts={i < 5 ? `${i + 1} ${o.label}` : undefined}
                onClick={() => (strikeMode ? toggleStrike(o.label) : void choose(o.label))}
                onContextMenu={(e) => {
                  e.preventDefault();
                  toggleStrike(o.label);
                }}
                className={cx(
                  OPTION,
                  on
                    ? "border-paper bg-paper text-ground"
                    : "border-line bg-ground text-text hover:border-line-control",
                )}
              >
                <span aria-hidden="true" className={cx(BADGE, on ? "bg-ground text-text" : "bg-surface-2 text-text")}>
                  {o.label}
                </span>
                <span
                  className={cx(
                    "exam-reading min-w-0 flex-1 !text-[length:inherit]",
                    off && "text-text-muted line-through",
                  )}
                >
                  <span className="sr-only">{words.join(", ")}: </span>
                  <SafeHtml html={o.html} className="inline [&_p]:m-0 [&_p]:inline" />
                </span>
                {off ? (
                  <span aria-hidden="true" className="shrink-0 font-mono text-label-sm text-text-3 uppercase">
                    {t("exam.option_struck")}
                  </span>
                ) : null}
              </button>
            );
          })}
        </fieldset>
        {queued.has(n) ? (
          <p role="status" className="m-0 rounded-block border border-line-control px-4 py-3 text-text-2">
            {t("exam.will_sync")}
          </p>
        ) : null}
        {sendError ? (
          <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
            {sendError}
          </p>
        ) : null}
        <div className="hidden flex-wrap justify-between gap-2 tablet:flex">
          <Button variant="secondary" size="cta" onClick={() => go(n - 1)} disabled={n <= firstN} className="px-6">
            {t("exam.previous")}
          </Button>
          {n < lastN ? (
            <Button variant="primary" size="cta" onClick={() => go(n + 1)} className="px-7">
              {t("exam.next")}
            </Button>
          ) : (
            <Button variant="primary" size="cta" onClick={() => setEnding(true)} className="px-7">
              {t(lastSection ? "exam.end_confirm_last" : "exam.end_block")}
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div {...viewAttrs} data-exam-root="" className={FRAME}>
      {header}
      {server.stale ? (
        <div className="px-4 pt-3 tablet:px-6">
          <OfflineBanner message={t("exam.clock_stale")} />
        </div>
      ) : null}
      <p aria-live="assertive" className="sr-only">
        {live}
      </p>
      <div className="flex flex-col gap-4 px-4 pt-4 pb-[calc(96px+env(safe-area-inset-bottom))] tablet:px-6 tablet:pb-10 laptop:flex-row laptop:items-start laptop:gap-5">
        <div className="hidden w-full rounded-[28px] border border-line bg-surface-1 p-4 tablet:block laptop:sticky laptop:top-24 laptop:w-[240px] laptop:shrink-0">
          <Navigator blockId={blockId} questions={sectionQs} firstN={firstN} currentN={n} chosenLocally={queued} />
        </div>
        <section
          aria-label={t("exam.question_region")}
          className="min-w-0 flex-1 rounded-[32px] border border-line bg-surface-1 px-5 py-6 tablet:px-[clamp(20px,4vw,56px)] tablet:py-10 laptop:max-w-[900px]"
        >
          {body}
        </section>
        <aside
          aria-label={t("exam.tools")}
          className="hidden flex-col gap-2 tablet:flex laptop:sticky laptop:top-24 laptop:w-[230px] laptop:shrink-0"
        >
          <div className="grid grid-cols-2 gap-2 laptop:grid-cols-1">{tools}</div>
          {spans.length ? (
            <button
              type="button"
              onClick={clearHighlights}
              className="min-h-11 rounded-pill border border-line-control px-4 text-left text-[0.8125rem] text-text-2"
            >
              {t("exam.clear_highlights")}
            </button>
          ) : null}
          <p className="m-0 px-1 pt-2 text-[0.8125rem] text-text-3">
            {block.config.mode === "exam" ? t("agent.exam_mode") : t("exam.assistant_timed")}{" "}
            {block.config.mode === "exam" ? t("exam.silent_note") : null}
          </p>
        </aside>
      </div>

      {/* Phone: progress, tools and Next in a bar (PhoneQbank board). */}
      <div className="fixed inset-x-0 bottom-0 z-10 flex items-center gap-2 border-line-soft border-t bg-ground/95 px-3 pt-2 pb-[max(10px,env(safe-area-inset-bottom))] backdrop-blur-[12px] tablet:hidden">
        <Button
          variant="secondary"
          onClick={toggleFlag}
          aria-pressed={view?.flagged ?? false}
          icon={<Flag size={16} fill={view?.flagged ? "currentColor" : "none"} />}
          className="min-h-14 px-3.5"
        >
          {view?.flagged ? t("exam.flagged") : t("exam.flag")}
        </Button>
        <Button
          variant="secondary"
          onClick={() => setTool("questions")}
          aria-label={t("exam.questions")}
          icon={<Grid3x3 size={16} />}
          className="min-h-14 px-3.5"
        />
        <Button
          variant="secondary"
          onClick={() => setTool("tools")}
          aria-label={t("exam.tools")}
          icon={<Wrench size={16} />}
          className="min-h-14 px-3.5"
        />
        {n < lastN ? (
          <Button variant="primary" onClick={() => go(n + 1)} className="min-h-14 flex-1">
            {t("exam.next")}
          </Button>
        ) : (
          <Button variant="primary" onClick={() => setEnding(true)} className="min-h-14 flex-1">
            {t("exam.end_block")}
          </Button>
        )}
      </div>

      <Sheet open={tool === "questions"} onClose={() => setTool(null)} title={t("exam.questions")}>
        <Navigator
          blockId={blockId}
          questions={sectionQs}
          firstN={firstN}
          currentN={n}
          chosenLocally={queued}
          onPick={() => setTool(null)}
          className="pb-2"
        />
      </Sheet>
      <Sheet open={tool === "tools"} onClose={() => setTool(null)} title={t("exam.tools")}>
        <div className="grid grid-cols-1 gap-2 pb-2">{makeTools(() => setTool(null))}</div>
        <p className="m-0 pt-3 text-[0.8125rem] text-text-3">
          {block.config.mode === "exam" ? t("agent.exam_mode") : t("exam.assistant_timed")}
        </p>
      </Sheet>
      <LabValuesSheet open={tool === "labs"} onClose={() => setTool(null)} />
      <Calculator open={tool === "calc"} onClose={() => setTool(null)} />
      <ScratchSheet open={tool === "scratch"} onClose={() => setTool(null)} blockId={blockId} n={n} />
      <ViewSheet t={t} open={tool === "view"} onClose={() => setTool(null)} prefs={prefs} onChange={setView} />
      <EndDialog
        t={t}
        open={ending}
        onClose={() => setEnding(false)}
        onConfirm={() =>
          lastSection
            ? finish.mutate(undefined, {
                onSuccess: (b) => server.adopt(b.clock),
                onError: (e) => setSendError(errorText(t, e)),
              })
            : transition("end")
        }
        last={lastSection}
        unanswered={unanswered}
        busy={action.isPending || finish.isPending}
      />
    </div>
  );
}

// ── The route component ──

/**
 * A timed or exam block (`/practice/blocks/$id/$n` for mode timed|exam). The server's clock decides which
 * screen shows: start, a running section (only its questions), paused (timed mode), the break, or Results.
 */
export function ExamPage({ blockId, n }: { blockId: string; n: number }) {
  const t = useCopy();
  const userId = useUserId();
  const block = useBlock(blockId);
  const server = useServerClock(blockId, block.data?.clock);
  const action = useSectionAction(blockId);
  const go = useNavigate();
  const now = useNow();
  const [error, setError] = useState<string | null>(null);
  useExamChrome(true);

  useEffect(() => server.adopt(block.data?.clock), [block.data?.clock, server.adopt]);
  useEffect(() => {
    if (userId) void sweep(userId);
  }, [userId]);

  const clock = server.clock ?? block.data?.clock ?? null;
  const state = clock?.state;
  // The block's question states follow the clock (a section ended elsewhere, a deadline passed).
  const refetch = block.refetch;
  // biome-ignore lint/correctness/useExhaustiveDependencies: refetch when the section changes too, not only the state
  useEffect(() => {
    if (state) void refetch();
  }, [state, clock?.section_n, refetch]);

  const breakMs = clock ? breakLeft(clock, server.offsetMs, now) : 0;
  useResyncAtZero(state === "break" ? breakMs : 1, server, `break:${clock?.section_n}`);

  const layout = useMemo(() => {
    const sections = block.data?.config.sections ?? [];
    return sections.find((s) => s.n === clock?.section_n) ?? null;
  }, [block.data?.config.sections, clock?.section_n]);

  if (block.isPending && !block.data) {
    return (
      <div className="flex flex-col gap-6 px-5 pt-10" aria-busy="true">
        <p role="status" className="sr-only">
          {t("exam.loading")}
        </p>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-64 rounded-tile" />
      </div>
    );
  }
  if (!block.data || !clock) {
    return (
      <section className="flex flex-col gap-6 px-5 pt-8">
        <ErrorState
          title={t("exam.error_title")}
          message={errorText(t, block.error)}
          requestId={block.error instanceof ApiError ? block.error.requestId : null}
          onRetry={() => void block.refetch()}
          retrying={block.isFetching}
        />
        <p className="m-0">
          <Link to="/practice">{t("exam.back_to_practice")}</Link>
        </p>
      </section>
    );
  }
  const b = block.data;

  if (state === "finished") {
    if (userId) void markFinished(userId, blockId, Date.now());
    return <Navigate to="/practice/blocks/$blockId/results" params={{ blockId }} replace />;
  }

  const start = (section: number) => {
    setError(null);
    action.mutate(
      { n: section, action: "start" },
      {
        onSuccess: (detail) => {
          server.adopt(detail.clock);
          void server.resync();
          const lay = detail.config.sections?.find((s) => s.n === section);
          if (lay)
            void go({ to: "/practice/blocks/$blockId/$n", params: { blockId, n: lay.first_index + 1 }, replace: true });
        },
        onError: (e) => {
          setError(errorText(t, e));
          void server.resync();
        },
      },
    );
  };

  if (state === "not_started") {
    return (
      <div data-exam-root="" className={FRAME}>
        <StartScreen t={t} block={b} clock={clock} onStart={() => start(1)} busy={action.isPending} error={error} />
      </div>
    );
  }
  if (state === "break") {
    return (
      <div data-exam-root="" className={FRAME}>
        <BreakScreen
          t={t}
          clock={clock}
          breakMs={breakMs}
          onStart={() => start(clock.section_n ?? 1)}
          busy={action.isPending}
          error={error}
          done={(clock.section_n ?? 1) - 1}
        />
      </div>
    );
  }
  if (!layout) return null;
  const firstN = layout.first_index + 1;
  const lastN = layout.first_index + layout.count;
  if (n < firstN || n > lastN) {
    // Only the running section is served: go to its first unanswered question.
    const target = b.questions.find((x) => x.n >= firstN && x.n <= lastN && !x.answered)?.n ?? firstN;
    return <Navigate to="/practice/blocks/$blockId/$n" params={{ blockId, n: target }} replace />;
  }
  if (state === "paused") {
    const left = sectionLeft(clock, server.offsetMs, now);
    return (
      <div data-exam-root="" className={FRAME}>
        <PausedScreen
          t={t}
          left={left}
          busy={action.isPending}
          onResume={() =>
            action.mutate(
              { n: layout.n, action: "resume" },
              {
                onSuccess: (d) => {
                  server.adopt(d.clock);
                  void server.resync();
                },
              },
            )
          }
        />
      </div>
    );
  }
  return (
    <Running
      key={`${layout.n}`}
      t={t}
      blockId={blockId}
      n={n}
      block={b}
      clock={clock}
      layout={layout}
      server={server}
      offsetMs={server.offsetMs}
      now={now}
    />
  );
}
