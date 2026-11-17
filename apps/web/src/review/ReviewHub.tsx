import { Button, Chip, ErrorState, OfflineBanner, Skeleton, Tile } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Check, RefreshCw } from "lucide-react";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  FILTERS,
  type QueueFilter,
  type ReviewQueueItem,
  requestIdOf,
  reviewErrorKey,
  useRecompute,
  useReviewItems,
  useReviewSummary,
} from "./api";
import { useReviewCopy } from "./copy";
import { DraftPanel } from "./DraftPanel";
import { awaitingMine } from "./logic";

function Row({ r }: { r: ReviewQueueItem }) {
  const t = useReviewCopy();
  return (
    <li>
      <Link
        to="/admin/review/$itemId"
        params={{ itemId: r.id }}
        data-testid="queue-row"
        data-item-id={r.id}
        className="flex min-h-11 flex-col gap-2 rounded-block border border-line-soft bg-surface-1 p-4 text-text no-underline hover:border-line-control hover:text-text tablet:p-5"
      >
        <span className="flex flex-wrap items-center gap-2">
          <Chip tone={r.origin === "ai" ? "assistant" : "meta"}>
            {r.origin === "ai" ? t("review.origin_ai") : t("review.origin_seed")}
          </Chip>
          <Chip tone={r.state === "adjudication" ? "warning" : "meta"}>{t(`review.state_${r.state}`)}</Chip>
          <Chip tone="meta">{t(`review.exam_${r.exam === "step2ck" ? "step2ck" : "step1"}`)}</Chip>
          {r.difficulty ? <Chip tone="meta">{t(`review.difficulty_${r.difficulty}`)}</Chip> : null}
          {r.mine_submitted ? (
            <Chip tone="done" icon={<Check size={12} />}>
              {t("review.you_reviewed")}
            </Chip>
          ) : null}
        </span>
        <span className="text-reading text-text [&_p]:m-0">{r.stem_preview}</span>
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-text-2 text-ui-sm">
          <span>{t("review.lint_errors", { n: r.errors })}</span>
          <span>{t("review.lint_warnings", { n: r.warnings })}</span>
          <span>{t("review.reviews_count", { submitted: r.submitted, needed: r.needed })}</span>
          {r.psych_flags.length ? (
            <span className="text-amber">{r.psych_flags.map((f) => t(`review.psych_flag.${f}`)).join(" · ")}</span>
          ) : null}
        </span>
      </Link>
    </li>
  );
}

function Queue({ filter }: { filter: QueueFilter }) {
  const t = useReviewCopy();
  const q = useReviewItems(filter);
  if (q.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3" data-testid="queue-loading">
        <p role="status" className="sr-only">
          {t("review.loading_queue")}
        </p>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28 w-full rounded-block" />
        ))}
      </div>
    );
  }
  if (q.isError) {
    return (
      <ErrorState
        title={t("review.queue_error_title")}
        message={t(reviewErrorKey(q.error))}
        requestId={requestIdOf(q.error)}
        onRetry={() => void q.refetch()}
        retryLabel={t("review.retry")}
        retrying={q.isFetching}
      />
    );
  }
  const rows = q.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) {
    return (
      <p className="m-0 rounded-block border border-line-soft px-5 py-6 text-text-2" data-testid="queue-empty">
        {t(`review.empty_${filter}`)}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <ul className="m-0 flex list-none flex-col gap-3 p-0" data-testid="queue-list">
        {rows.map((r) => (
          <Row key={r.id} r={r} />
        ))}
      </ul>
      {q.hasNextPage ? (
        <div>
          <Button variant="secondary" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
            {q.isFetchingNextPage ? t("review.loading_more") : t("review.load_more")}
          </Button>
        </div>
      ) : null}
      {q.isFetchNextPageError ? (
        <p role="alert" className="m-0 text-amber">
          {t(reviewErrorKey(null))}
        </p>
      ) : null}
    </div>
  );
}

export function ReviewHub({ filter, onFilter }: { filter: QueueFilter; onFilter: (f: QueueFilter) => void }) {
  const t = useReviewCopy();
  const nav = useNavigate();
  const online = useOnlineStatus();
  const summary = useReviewSummary();
  const list = useReviewItems(filter);
  const recompute = useRecompute();
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];
  const next = filter === "psychometric_flags" ? undefined : rows.find(awaitingMine);

  return (
    <div className="flex flex-col gap-6 pt-4 tablet:pt-6" data-testid="review-hub">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="m-0 font-display font-bold text-[clamp(1.75rem,1.2rem+2.2vw,2.75rem)] leading-none tracking-[-0.03em]">
            {t("review.title")}
          </h1>
          <p className="m-0 max-w-[60ch] text-text-2">
            {summary.data && summary.data.required_approvals >= 2 ? t("review.intro_two") : t("review.intro_one")}
          </p>
        </div>
        <Button
          variant="primary"
          size="cta"
          disabled={!next}
          aria-describedby="review-next-why"
          onClick={() => next && void nav({ to: "/admin/review/$itemId", params: { itemId: next.id } })}
        >
          {t("review.review_next")}
        </Button>
      </header>
      <p id="review-next-why" className="m-0 -mt-3 text-text-3 text-ui-sm">
        {next ? t("review.next_hint") : list.isPending ? t("review.loading_queue") : t("review.next_none")}
      </p>
      {online ? null : <OfflineBanner message={t("review.offline")} />}

      <DraftPanel />

      <Tile as="section" aria-labelledby="rv-queue-h" className="flex flex-col gap-5">
        <h2 id="rv-queue-h" className="sr-only">
          {t("review.queue_title")}
        </h2>
        <nav aria-label={t("review.filters_label")}>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {FILTERS.map((f) => {
              const n = summary.data?.[f];
              const current = f === filter;
              return (
                <li key={f}>
                  <button
                    type="button"
                    aria-current={current ? "true" : undefined}
                    data-filter={f}
                    onClick={() => onFilter(f)}
                    className={`inline-flex min-h-11 items-center gap-2 rounded-pill border px-4 text-ui-sm ${
                      current
                        ? "border-text font-semibold text-text"
                        : "border-line-control bg-transparent text-text-2 hover:bg-surface-2"
                    }`}
                  >
                    {t(`review.filter_${f}`)}
                    <span className="font-mono text-label-sm" data-testid={`count-${f}`}>
                      {n === undefined ? "–" : n}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
        {summary.isError ? (
          <p role="alert" className="m-0 text-amber text-ui-sm">
            {t("review.summary_error")}
          </p>
        ) : null}
        <Queue filter={filter} />
      </Tile>

      <Tile as="section" aria-labelledby="rv-stats-h" className="flex flex-col gap-3">
        <h2 id="rv-stats-h" className="m-0 font-mono text-label text-text-3 uppercase">
          {t("review.recompute_title")}
        </h2>
        <p className="m-0 text-text-2 text-ui-sm">{t("review.recompute_body")}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            disabled={recompute.isPending}
            onClick={() => recompute.mutate()}
            icon={<RefreshCw size={16} className={recompute.isPending ? "animate-spin" : undefined} />}
          >
            {recompute.isPending ? t("review.recomputing") : t("review.recompute")}
          </Button>
          {recompute.data ? (
            <span role="status" className="text-text-2 text-ui-sm">
              {t("review.recomputed", {
                items: recompute.data.items,
                flagged: recompute.data.flagged,
              })}
            </span>
          ) : null}
          {recompute.error ? (
            <span role="alert" className="text-amber text-ui-sm">
              {t(reviewErrorKey(recompute.error))}
            </span>
          ) : null}
        </div>
      </Tile>
    </div>
  );
}
