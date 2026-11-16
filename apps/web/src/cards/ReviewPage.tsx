import type { CardOut, DueResponse, ReviewIn } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { Eraser, PenLine, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { ContentRenderer } from "../content/ContentRenderer";
import { InkCanvas } from "../ink/InkCanvas";
import { errorKey, isUnreachable, sendReview, useDueCards, usePendingReviews } from "./api";
import { CardDialog, type CardDraft } from "./CardDialog";
import { useCardsCopy } from "./copy";
import { cardKey, intervalLabel, ownedElsewhere, whenLabel } from "./format";
import {
  type CardMemory,
  configFromServer,
  memoryFromSchedule,
  RATINGS,
  type Rating,
  type ReviewOutcome,
  Scheduler,
} from "./fsrs";

/** A learning card due within this window comes back in the same session (as Anki's learn-ahead limit). */
const LEARN_AHEAD_MS = 20 * 60_000;
const RATING_KEYS: Record<Rating, string> = {
  1: "cards.rating_again",
  2: "cards.rating_hard",
  3: "cards.rating_good",
  4: "cards.rating_easy",
};
const RATING_STYLE: Record<Rating, string> = {
  1: "border-2 border-amber bg-transparent text-amber font-semibold hover:bg-amber/10",
  2: "border border-line-control bg-transparent text-text hover:bg-surface-2",
  3: "border border-transparent bg-paper text-ground font-semibold hover:bg-white",
  4: "border border-transparent bg-lime text-lime-ink font-semibold hover:bg-lime-hover",
};
const ORIGIN_KEYS: Record<CardOut["origin"], string> = {
  user: "cards.origin_user",
  assistant: "cards.origin_assistant",
  anki_import: "cards.origin_anki_import",
  seed: "cards.origin_seed",
};

function uuid(): string {
  return crypto.randomUUID();
}

function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

interface Session {
  /** Card ids still to show, in order (a learning card due soon is appended again). */
  queue: string[];
  cards: Record<string, CardOut>;
  memory: Record<string, CardMemory>;
  /** Distinct cards in this session, and how many have left it. */
  total: number;
  done: number;
  /** Ratings made in this session (for the summary). */
  rated: number;
}

function startSession(due: DueResponse, previous?: Session): Session {
  const cards: Record<string, CardOut> = {};
  const memory: Record<string, CardMemory> = {};
  for (const c of due.cards) {
    cards[c.id] = c;
    memory[c.id] = memoryFromSchedule(c.schedule);
  }
  return {
    queue: due.cards.map((c) => c.id),
    cards,
    memory,
    total: due.cards.length + (previous?.done ?? 0),
    done: previous?.done ?? 0,
    rated: previous?.rated ?? 0,
  };
}

/** Big display type for short faces, reading size for long ones (a face can be up to 4000 characters). */
function faceSize(text: string): string {
  if (text.length <= 90)
    return "[&_p]:font-display [&_p]:font-extrabold [&_p]:text-[clamp(1.75rem,0.9rem+3.6vw,4.25rem)]! [&_p]:leading-[1.02]! [&_p]:tracking-[-0.035em]";
  if (text.length <= 280) return "[&_p]:font-display [&_p]:font-bold [&_p]:text-lead! [&_p]:leading-[1.15]!";
  return "[&_p]:text-reading!";
}

function SourceLink({ card }: { card: CardOut }) {
  const t = useCardsCopy();
  const s = card.source;
  if (!s) return null;
  const cls =
    "inline-flex min-h-11 items-center rounded-pill border-[1.5px] border-ground px-4 text-ground text-ui-sm no-underline hover:bg-ground/5 hover:text-ground";
  if (s.kind === "section" && s.chapter_id) {
    return (
      <Link to="/learn/$chapterId/$sectionId" params={{ chapterId: s.chapter_id, sectionId: s.id }} className={cls}>
        {t("cards.open_source")}
      </Link>
    );
  }
  if (s.kind === "question") {
    return (
      <Link to="/practice/question/$questionId" params={{ questionId: s.id }} className={cls}>
        {t("cards.open_source")}
      </Link>
    );
  }
  return null;
}

function ScratchPad({ cardId }: { cardId: string }) {
  const t = useCardsCopy();
  const [writing, setWriting] = useState(true);
  const [generation, setGeneration] = useState(0);
  return (
    <div className="flex min-w-0 flex-[2_1_360px] flex-col gap-3 rounded-tile-sm border border-line-soft bg-surface-1 p-4 [background-image:radial-gradient(var(--color-line-strong)_1.2px,transparent_1.2px)] [background-size:22px_22px]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-label-sm text-text-3 uppercase tracking-[0.1em]">{t("cards.scratch")}</span>
        <span className="flex gap-2">
          <button
            type="button"
            aria-pressed={writing}
            onClick={() => setWriting((w) => !w)}
            className={cx(
              "inline-flex min-h-11 items-center gap-1.5 rounded-pill border px-3.5 text-ui-sm",
              writing ? "border-paper bg-paper text-ground" : "border-line-control text-text",
            )}
          >
            <PenLine aria-hidden="true" size={16} />
            {t("cards.scratch_write")}
          </button>
          <button
            type="button"
            onClick={() => setGeneration((g) => g + 1)}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-pill border border-line-control px-3.5 text-text text-ui-sm"
          >
            <Eraser aria-hidden="true" size={16} />
            {t("cards.scratch_clear")}
          </button>
        </span>
      </div>
      {/* No persistence yet (segment 12b): the pad clears for each card. */}
      <InkCanvas
        key={`${cardId}-${generation}`}
        enabled={writing}
        surfaceType="flashcard"
        theme="dark"
        label={t("cards.scratch_label")}
        className="h-44 rounded-block tablet:h-52"
      />
    </div>
  );
}

function StuckPanel({ card }: { card: CardOut }) {
  const t = useCardsCopy();
  // The assistant (segment 14) listens for this event and answers in its panel with the card as page context.
  const ask = (kind: "hint" | "explain") =>
    window.dispatchEvent(
      new CustomEvent("naadi:assistant-ask", { detail: { kind, context: { type: "flashcard", id: card.id } } }),
    );
  return (
    <aside
      aria-label={t("cards.stuck")}
      className="flex min-w-0 flex-[1_1_260px] flex-col gap-2.5 rounded-tile-sm border border-violet-line bg-violet-ground p-6"
    >
      <span className="font-mono text-label text-violet-text uppercase">{t("cards.stuck")}</span>
      <Button variant="strong" onClick={() => ask("hint")}>
        {t("cards.hint")}
      </Button>
      <Button variant="secondary" onClick={() => ask("explain")} className="border-violet-line text-violet-text">
        {t("cards.explain")}
      </Button>
    </aside>
  );
}

export function ReviewPage() {
  const t = useCardsCopy();
  const due = useDueCards();
  const me = useMe();
  const online = useOnline();
  const pending = usePendingReviews();
  const assistantOn = me.data?.features.assistant === true;
  const [session, setSession] = useState<Session | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [shownAt, setShownAt] = useState(() => Date.now());
  const [dialog, setDialog] = useState<{ card?: CardOut; draft?: CardDraft } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const cardRef = useRef<HTMLElement>(null);

  // A new session from each fresh due list (first load, "Review more", a card created on an empty screen).
  const data = due.data?.data;
  useEffect(() => {
    if (data) setSession((prev) => (prev && prev.queue.length > 0 ? prev : startSession(data, prev ?? undefined)));
  }, [data]);

  const scheduler = useMemo(() => (data ? new Scheduler(configFromServer(data.scheduler)) : null), [data]);
  const currentId = session?.queue[0] ?? null;
  const current = currentId ? session?.cards[currentId] : undefined;
  const memory = currentId ? session?.memory[currentId] : undefined;
  const [previewAt, setPreviewAt] = useState(() => new Date());
  const preview = useMemo(
    () => (scheduler && current && memory ? scheduler.preview(current.id, memory, previewAt) : null),
    [scheduler, current, memory, previewAt],
  );

  const flip = useCallback(() => {
    setPreviewAt(new Date());
    setFlipped(true);
  }, []);

  const send = useCallback(
    (cardId: string, body: ReviewIn) => {
      // One request at a time, in order: a learning card rated twice must reach the server in that order.
      chain.current = chain.current.then(async () => {
        if (pending.count > 0 || !navigator.onLine) {
          pending.enqueue(cardId, body);
          void pending.flush();
          return;
        }
        try {
          const out = await sendReview(cardId, body);
          setSession((s) =>
            s?.memory[cardId] ? { ...s, memory: { ...s.memory, [cardId]: memoryFromSchedule(out.schedule) } } : s,
          );
        } catch (err) {
          if (isUnreachable(err) || (err instanceof ApiError && (err.status ?? 500) >= 500)) {
            pending.enqueue(cardId, body);
          } else {
            setNotice(t(errorKey(err)));
          }
        }
      });
    },
    [pending, t],
  );

  const rate = useCallback(
    (rating: Rating) => {
      if (!session || !current || !scheduler || !memory || !flipped) return;
      const now = new Date();
      const outcome: ReviewOutcome = scheduler.review(current.id, memory, rating, now);
      send(current.id, {
        client_review_id: uuid(),
        rating,
        reviewed_at: now.toISOString(),
        duration_ms: Math.min(3_600_000, Math.max(0, now.getTime() - shownAt)),
      });
      setSession((s) => {
        if (!s) return s;
        const rest = s.queue.slice(1);
        const again = outcome.intervalMs <= LEARN_AHEAD_MS;
        return {
          ...s,
          queue: again ? [...rest, current.id] : rest,
          memory: { ...s.memory, [current.id]: outcome.card },
          done: again ? s.done : s.done + 1,
          rated: s.rated + 1,
        };
      });
      setFlipped(false);
      setShownAt(Date.now());
      setNotice(null);
      cardRef.current?.focus({ preventScroll: true });
    },
    [current, flipped, memory, scheduler, send, session, shownAt],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || dialog || !current || ownedElsewhere(e.target, e.key)) return;
      const action = cardKey(e, flipped);
      if (!action) return;
      e.preventDefault();
      if (action.type === "flip") flip();
      else rate(action.rating);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, dialog, flip, flipped, rate]);

  // A notice ("Card saved", a refused rating) is shown for a few seconds.
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const fromCache = due.data?.fromCache === true;
  const offlineNote =
    !online || fromCache ? (
      <p role="status" className="m-0 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm">
        {t("cards.offline")}
      </p>
    ) : null;
  const pendingNote =
    pending.count > 0 ? (
      <div
        role="status"
        className="flex flex-wrap items-center gap-3 rounded-block border border-line-control px-4 py-2"
      >
        <span className="text-text-2 text-ui-sm">
          {pending.count === 1 ? t("cards.pending_one") : t("cards.pending", { n: pending.count })}
        </span>
        {online ? (
          <Button variant="secondary" onClick={() => void pending.flush()} disabled={pending.syncing}>
            {pending.syncing ? t("cards.syncing") : t("cards.sync_now")}
          </Button>
        ) : null}
      </div>
    ) : null;

  // Making or editing a card needs the server; offline the button stays visible, disabled, and the offline note says why.
  const newCardButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<Plus size={18} />} onClick={() => setDialog({})} disabled={!online}>
      {t("cards.new_card")}
    </Button>
  );

  const dialogEl = (
    <CardDialog
      open={dialog !== null}
      card={dialog?.card}
      draft={dialog?.draft}
      onClose={() => setDialog(null)}
      onSaved={(saved) => {
        setNotice(t("cards.saved"));
        setSession((s) => {
          if (!s) return s;
          if (s.cards[saved.id]) return { ...s, cards: { ...s.cards, [saved.id]: saved } };
          // A new card is due now: it joins this session at the end.
          return {
            ...s,
            queue: [...s.queue, saved.id],
            cards: { ...s.cards, [saved.id]: saved },
            memory: { ...s.memory, [saved.id]: memoryFromSchedule(saved.schedule) },
            total: s.total + 1,
          };
        });
      }}
      onDeleted={(id) => {
        setFlipped(false);
        setSession((s) =>
          s ? { ...s, queue: s.queue.filter((q) => q !== id), total: Math.max(s.done, s.total - 1) } : s,
        );
      }}
    />
  );

  const live = (
    <p
      role="status"
      aria-live="polite"
      className={notice ? "m-0 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm" : "sr-only"}
    >
      {notice ?? ""}
    </p>
  );
  const heading = <h1 className="sr-only">{t("cards.title")}</h1>;

  // ── loading / error / offline without a saved copy ──
  if (!session) {
    if (due.isError) {
      const err = due.error;
      return (
        <section className="flex flex-col gap-6 pt-8">
          {heading}
          {isUnreachable(err) ? (
            <p role="status" className="m-0 rounded-block border border-line-control px-4 py-3 text-text-2">
              {t("cards.offline_none")}
            </p>
          ) : (
            <ErrorState
              title={t("cards.error_title")}
              message={t(errorKey(err))}
              requestId={err instanceof ApiError ? err.requestId : null}
              onRetry={() => void due.refetch()}
              retrying={due.isFetching}
              retryLabel={t("cards.retry")}
            />
          )}
          {pendingNote}
        </section>
      );
    }
    return (
      <section aria-busy="true" className="flex flex-col gap-6 pt-8">
        {heading}
        <p className="sr-only" role="status">
          {t("cards.loading")}
        </p>
        <Skeleton className="h-12 w-full rounded-pill" />
        <Skeleton className="h-[420px] w-full rounded-sheet" />
        <Skeleton className="h-[68px] w-full rounded-pill" />
      </section>
    );
  }

  // ── nothing (left) to review ──
  if (!current) {
    const reviewedSome = session.rated > 0;
    // The earliest due time ahead: the server's (cards not in this session) or a card just rated here.
    const nowMs = Date.now();
    const dues = [
      ...Object.values(session.memory).map((m) => m.due?.getTime() ?? 0),
      due.data?.data.next_due ? new Date(due.data.data.next_due).getTime() : 0,
    ].filter((ms) => ms > nowMs);
    const next = dues.length ? new Date(Math.min(...dues)).toISOString() : null;
    return (
      <section className="mx-auto flex w-full max-w-[980px] flex-col gap-6 pt-8 pb-[calc(var(--shell-bottom,0px)+32px)]">
        {heading}
        {offlineNote}
        {pendingNote}
        <div className="flex flex-col gap-4 rounded-sheet border border-line bg-surface-1 p-[clamp(28px,5vw,64px)]">
          <h2 className="m-0 font-display text-headline">
            {reviewedSome ? t("cards.done_title") : t("cards.empty_title")}
          </h2>
          <p className="m-0 text-reading text-text-2">
            {reviewedSome
              ? session.done === 1
                ? t("cards.done_body_one")
                : t("cards.done_body", { n: session.done })
              : next
                ? t("cards.empty_next", { when: whenLabel(new Date(next)) })
                : t("cards.empty_none")}
          </p>
          {reviewedSome && next ? (
            <p className="m-0 text-text-2">{t("cards.empty_next", { when: whenLabel(new Date(next)) })}</p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            {newCardButton("primary")}
            {reviewedSome && online ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setSession(null);
                  void due.refetch();
                }}
              >
                {t("cards.more")}
              </Button>
            ) : null}
          </div>
        </div>
        {live}
        {dialogEl}
      </section>
    );
  }

  // ── reviewing ──
  const pct = session.total ? Math.round((100 * session.done) / session.total) : 0;
  const face = flipped ? current.back : current.front;
  const faceText = flipped ? current.back_text : current.front_text;
  return (
    <section
      data-card-shortcuts="on"
      className="mx-auto flex w-full max-w-[980px] flex-col gap-6 pt-6 pb-[calc(var(--shell-bottom,0px)+32px)] tablet:pt-10"
    >
      {heading}
      <header className="flex flex-wrap items-center gap-4">
        <span className="font-display text-[2.5rem] leading-none font-extrabold tracking-[-0.03em]" aria-hidden="true">
          {session.done} <span className="text-text-muted">{t("cards.progress_total", { total: session.total })}</span>
        </span>
        <div
          role="img"
          aria-label={t("cards.progress", { done: session.done, total: session.total })}
          className="h-1.5 min-w-24 flex-[1_1_200px] rounded-[3px] bg-line"
        >
          <div className="h-1.5 rounded-[3px] bg-paper" style={{ width: `${pct}%` }} />
        </div>
        <Chip tone="meta">{t("cards.deck_chip", { deck: current.deck, origin: t(ORIGIN_KEYS[current.origin]) })}</Chip>
        {current.editable ? (
          <Button variant="secondary" onClick={() => setDialog({ card: current })} disabled={!online}>
            {t("cards.edit")}
          </Button>
        ) : null}
        {newCardButton("secondary")}
      </header>
      {offlineNote}
      {pendingNote}

      <section
        ref={cardRef}
        tabIndex={-1}
        aria-label={t("cards.card_label")}
        aria-live="polite"
        className={cx(
          "flex min-h-[420px] flex-col justify-between gap-7 rounded-sheet p-[clamp(28px,5vw,64px)] outline-none",
          flipped ? "bg-paper text-ground" : "border border-line bg-surface-1 text-text",
        )}
      >
        <span className="font-mono text-label uppercase tracking-[0.14em] opacity-70">
          {flipped ? t("cards.side_back") : t("cards.side_front")}
        </span>
        <ContentRenderer
          key={`${current.id}-${flipped ? "back" : "front"}`}
          doc={face}
          sectionId={`card-${current.id}-${flipped ? "back" : "front"}`}
          copy={(key) => t(key)}
          headingBase={3}
          className={cx(faceSize(faceText), flipped ? "text-ground! [&_strong]:text-ground!" : "text-text!")}
        />
        {flipped ? (
          <div className="flex flex-wrap items-center gap-4">
            <SourceLink card={current} />
          </div>
        ) : (
          <span className="text-ui opacity-70">{t("cards.front_hint")}</span>
        )}
      </section>

      {flipped && preview ? (
        <fieldset className="m-0 min-w-0 border-0 p-0">
          <legend className="sr-only">{t("cards.ratings_label")}</legend>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2.5">
            {RATINGS.map((r) => (
              <button
                key={r}
                type="button"
                data-rating={r}
                onClick={() => rate(r)}
                aria-keyshortcuts={String(r)}
                className={cx("min-h-[68px] rounded-[22px] px-3 text-[1.0625rem]", RATING_STYLE[r])}
              >
                {t("cards.rating_with_interval", {
                  rating: t(RATING_KEYS[r]),
                  interval: intervalLabel(preview[r].intervalMs, t),
                })}
              </button>
            ))}
          </div>
        </fieldset>
      ) : (
        <Button variant="primary" onClick={flip} aria-keyshortcuts="Space" className="min-h-[68px]! text-[1.1875rem]!">
          {t("cards.show_answer")}
        </Button>
      )}
      <p className="m-0 hidden text-text-3 text-ui-sm laptop:block">{t("cards.keys_hint")}</p>

      <div className="flex flex-wrap gap-5">
        <ScratchPad key={`${current.id}-${session.rated}`} cardId={current.id} />
        {assistantOn ? <StuckPanel card={current} /> : null}
      </div>
      {live}
      {dialogEl}
    </section>
  );
}
