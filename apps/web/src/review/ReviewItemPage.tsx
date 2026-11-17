import { listReviewItems } from "@naadi/shared";
import { Button, Chip, ErrorState, OfflineBanner, Skeleton, Tile } from "@naadi/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  type QueueFilter,
  requestIdOf,
  reviewErrorKey,
  reviewKey,
  useResubmit,
  useReviewItem,
  useSubmitReview,
} from "./api";
import { useReviewCopy } from "./copy";
import { FindingList, Provenance, SolverBlock } from "./Findings";
import { GatesPanel } from "./Gates";
import { ItemPreview } from "./ItemPreview";
import { awaitingMine, type OutcomeKey } from "./logic";
import { PsychometricsPanel } from "./Psychometrics";
import { RatingForm } from "./RatingForm";
import { ReviewsPanel } from "./ReviewsPanel";
import { H2 } from "./ui";

function Loading() {
  const t = useReviewCopy();
  return (
    <section aria-busy="true" className="flex flex-col gap-6 pt-4" data-testid="review-item-loading">
      <p role="status" className="sr-only">
        {t("review.loading_item")}
      </p>
      <Skeleton className="h-8 w-1/2" />
      <Skeleton className="h-40 rounded-block" />
      {[0, 1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-[68px] rounded-block" />
      ))}
    </section>
  );
}

/** The queue filter an item belongs to, so "next item" stays in the same list. */
function filterOf(origin: "ai" | "seed", state: string): QueueFilter {
  if (state === "adjudication") return "adjudication";
  return origin === "seed" ? "seed_spot_check" : "ai_drafts";
}

function OutcomePanel({
  outcome,
  itemId,
  filter,
  seed,
}: {
  outcome: OutcomeKey;
  itemId: string;
  filter: QueueFilter;
  seed: boolean;
}) {
  const t = useReviewCopy();
  const nav = useNavigate();
  const auth = useAuth();
  const qc = useQueryClient();
  const [looking, setLooking] = useState(false);
  const [none, setNone] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  // The outcome replaces the form the reviewer was looking at: bring it into view and announce it.
  useEffect(() => {
    panel.current?.scrollIntoView?.({ block: "start" });
    panel.current?.focus({ preventScroll: true });
  }, []);
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;

  async function next() {
    setLooking(true);
    setNone(false);
    try {
      const list = await qc.fetchQuery({
        queryKey: [...reviewKey(userId), "next", filter, itemId, Date.now()],
        staleTime: 0,
        queryFn: () => authed((headers) => listReviewItems({ headers, query: { filter, limit: 100, offset: 0 } })),
      });
      const row = list.items.find((r) => r.id !== itemId && awaitingMine(r));
      if (row) await nav({ to: "/admin/review/$itemId", params: { itemId: row.id } });
      else setNone(true);
    } catch {
      setNone(true);
    } finally {
      setLooking(false);
    }
  }

  return (
    <div ref={panel} tabIndex={-1} className="scroll-mt-24 outline-none">
      <Tile
        as="section"
        aria-labelledby="rv-outcome-h"
        className="flex flex-col gap-4"
        data-testid="review-outcome"
        data-outcome={outcome}
      >
        <h2 id="rv-outcome-h" className={H2}>
          {t(`review.outcome_${outcome}_title`)}
        </h2>
        <p className="m-0 text-text-2">
          {t(seed && outcome === "published" ? "review.outcome_seed_published_body" : `review.outcome_${outcome}_body`)}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" size="cta" disabled={looking} onClick={() => void next()}>
            {t("review.next_item")}
          </Button>
          <Link
            to="/admin/review"
            className="inline-flex min-h-12 items-center rounded-pill border border-line-control px-5 text-text no-underline hover:bg-surface-2 hover:text-text"
          >
            {t("review.back_to_queue")}
          </Link>
        </div>
        {none ? (
          <p role="status" className="m-0 text-text-2" data-testid="review-no-next">
            {t("review.nothing_next")}
          </p>
        ) : null}
      </Tile>
    </div>
  );
}

export function ReviewItemPage({ itemId }: { itemId: string }) {
  const t = useReviewCopy();
  const online = useOnlineStatus();
  const q = useReviewItem(itemId);
  const qc = useQueryClient();
  const auth = useAuth();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const submit = useSubmitReview(itemId);
  const resubmit = useResubmit(itemId);
  const [outcome, setOutcome] = useState<OutcomeKey | null>(null);
  const [filter, setFilter] = useState<QueueFilter>("ai_drafts");

  // A new item resets the outcome of the previous one.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when the id changes
  useEffect(() => {
    setOutcome(null);
    submit.reset();
    resubmit.reset();
  }, [itemId]);

  // Another admin's action (or a closed round) makes the page stale: re-read it.
  const failed = submit.error instanceof ApiError ? submit.error.code : null;
  useEffect(() => {
    if (failed === "round_closed" || failed === "already_reviewed" || failed === "publish_blocked") {
      void qc.invalidateQueries({ queryKey: [...reviewKey(userId), "item", itemId] });
    }
  }, [failed, qc, userId, itemId]);

  const back = (
    <Link
      to="/admin/review"
      className="inline-flex min-h-11 items-center gap-2 self-start text-text-2 text-ui-sm no-underline hover:text-text"
    >
      <ArrowLeft aria-hidden="true" size={16} />
      {t("review.back_to_queue")}
    </Link>
  );

  if (q.isPending) return <Loading />;
  if (!q.data) {
    const gone = q.error instanceof ApiError && q.error.status === 404;
    return (
      <section className="flex flex-col gap-5 pt-6">
        {back}
        {online ? null : <OfflineBanner message={t("review.offline")} />}
        <ErrorState
          title={gone ? t("review.item_gone_title") : t("review.item_error_title")}
          message={t(reviewErrorKey(q.error))}
          requestId={requestIdOf(q.error)}
          onRetry={gone ? undefined : () => void q.refetch()}
          retryLabel={t("review.retry")}
          retrying={q.isFetching}
        />
      </section>
    );
  }
  const item = q.data;
  const published = item.status === "published";
  const seedSpot = item.origin === "seed" && published && item.can_review.allowed;
  const showForm = !outcome && (item.status === "in_review" || item.status === "auto_checked" || seedSpot);

  return (
    <div className="flex flex-col gap-6 pt-4 tablet:pt-6" data-testid="review-item" data-item-status={item.status}>
      {back}
      {online ? null : <OfflineBanner message={t("review.offline")} />}
      <header className="flex flex-col gap-3">
        <h1 className="m-0 font-display font-bold text-[clamp(1.5rem,1.1rem+1.4vw,2.25rem)] tracking-[-0.02em]">
          {t("review.item_title")}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={item.origin === "ai" ? "assistant" : "meta"}>
            {item.origin === "ai" ? t("review.origin_ai") : t("review.origin_seed")}
          </Chip>
          <Chip tone="meta">{t(`review.status_${item.status}`)}</Chip>
          <Chip tone="meta">{item.exam === "step2ck" ? t("review.exam_step2ck") : t("review.exam_step1")}</Chip>
          {item.difficulty ? <Chip tone="meta">{t(`review.difficulty_${item.difficulty}`)}</Chip> : null}
          <span className="break-all font-mono text-label-sm text-text-3">{item.id}</span>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] laptop:items-start laptop:gap-8">
        <ItemPreview item={item} />
        <div className="flex flex-col gap-6">
          {item.status === "draft" ? (
            <Tile as="section" aria-labelledby="rv-draft-h" className="flex flex-col gap-4" data-testid="review-draft">
              <h2 id="rv-draft-h" className={H2}>
                {t("review.draft_title")}
              </h2>
              <p className="m-0 text-text-2">{t("review.draft_body")}</p>
              <div>
                <Button variant="primary" size="cta" disabled={resubmit.isPending} onClick={() => resubmit.mutate()}>
                  {resubmit.isPending ? t("review.resubmitting") : t("review.resubmit")}
                </Button>
              </div>
              {resubmit.error ? (
                <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3">
                  {t(
                    resubmit.error instanceof ApiError && resubmit.error.code === "publish_blocked"
                      ? "review.error.resubmit_blocked"
                      : reviewErrorKey(resubmit.error),
                  )}
                </p>
              ) : null}
            </Tile>
          ) : null}
          {outcome ? (
            <OutcomePanel outcome={outcome} itemId={item.id} filter={filter} seed={item.origin === "seed"} />
          ) : null}
          <Provenance item={item} />
          <Tile as="section" aria-labelledby="rv-lint-h" className="flex flex-col gap-4" data-testid="review-lint">
            <h2 id="rv-lint-h" className="m-0 font-mono text-label text-text-3 uppercase">
              {t("review.lint_title")}
            </h2>
            {item.lint.length ? (
              <FindingList findings={item.lint} />
            ) : (
              <p className="m-0 text-text-2" data-testid="review-lint-clean">
                {t("review.lint_clean")}
              </p>
            )}
          </Tile>
          <SolverBlock item={item} />
          <GatesPanel item={item} canRun={item.status !== "retired"} />
          {showForm ? (
            <RatingForm
              key={`${item.id}-${item.resolution.round}`}
              item={item}
              pending={submit.isPending}
              error={submit.error}
              onSubmit={(body) =>
                submit.mutate(body, {
                  onSuccess: (r) => {
                    setFilter(filterOf(item.origin, item.resolution.state));
                    setOutcome(r.outcome);
                  },
                })
              }
            />
          ) : null}
          <ReviewsPanel item={item} />
          {published || item.stats ? <PsychometricsPanel item={item} /> : null}
        </div>
      </div>
    </div>
  );
}
