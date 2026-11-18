import type { ReviewEntryOut, ReviewItemDetail } from "@naadi/shared";
import { Chip, Tile, TileLabel } from "@naadi/ui";
import { Gavel, Lock } from "lucide-react";
import { useReviewCopy } from "./copy";

function Entry({ r }: { r: ReviewEntryOut }) {
  const t = useReviewCopy();
  const x = r.ratings;
  return (
    <li
      className="flex flex-col gap-2 rounded-block border border-line-soft bg-surface-2 p-4"
      data-testid="review-entry"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={r.decision === "approve" ? "done" : "weak"}>{t(`review.decision_${r.decision}`)}</Chip>
        <span className="font-mono text-label text-text-3 uppercase">
          {t("review.round_n", { n: r.round })} · {r.is_mine ? t("review.you") : t("review.other_reviewer")}
          {r.adjudication ? ` · ${t("review.adjudicator")}` : ""}
        </span>
        {r.request_second ? <Chip tone="meta">{t("review.asked_second")}</Chip> : null}
      </div>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-ui-sm">
        {x.accuracy ? (
          <>
            <dt className="text-text-3">{t("review.rating_accuracy")}</dt>
            <dd className="m-0">{t(`review.accuracy_${x.accuracy}`)}</dd>
          </>
        ) : null}
        {x.single_best_answer !== null && x.single_best_answer !== undefined ? (
          <>
            <dt className="text-text-3">{t("review.rating_single")}</dt>
            <dd className="m-0">{x.single_best_answer ? t("review.yes") : t("review.no")}</dd>
          </>
        ) : null}
        {x.cognitive_level ? (
          <>
            <dt className="text-text-3">{t("review.rating_cognitive")}</dt>
            <dd className="m-0">{t(`review.cognitive_${x.cognitive_level}`)}</dd>
          </>
        ) : null}
        {x.difficulty ? (
          <>
            <dt className="text-text-3">{t("review.rating_difficulty")}</dt>
            <dd className="m-0">{t(`review.difficulty_${x.difficulty}`)}</dd>
          </>
        ) : null}
        {x.taxonomy_ok !== null && x.taxonomy_ok !== undefined ? (
          <>
            <dt className="text-text-3">{t("review.rating_taxonomy")}</dt>
            <dd className="m-0">{x.taxonomy_ok ? t("review.yes") : t("review.no")}</dd>
          </>
        ) : null}
        <dt className="text-text-3">{t("review.rating_flaws")}</dt>
        <dd className="m-0">
          {(x.flaws ?? []).length
            ? (x.flaws ?? []).map((f) => t(`review.flaw.${f}`)).join("; ")
            : t("review.rating_no_flaws")}
        </dd>
      </dl>
      {r.note ? (
        <p className="m-0 rounded-inner bg-paper px-3 py-2 text-ground text-ui-sm" data-testid="review-note">
          {r.note}
        </p>
      ) : null}
    </li>
  );
}

/** Past rounds and the reviews this admin may see. Blindness: another reviewer's review is hidden until both have submitted. */
export function ReviewsPanel({ item }: { item: ReviewItemDetail }) {
  const t = useReviewCopy();
  const res = item.resolution;
  const adjudicating = res.state === "adjudication" && item.can_review.allowed;
  const waitingThird = res.state === "adjudication" && !item.can_review.allowed;
  const rounds = [...item.reviews].sort((a, b) => b.round - a.round || a.id - b.id);
  return (
    <Tile as="section" aria-labelledby="rv-reviews-h" className="flex flex-col gap-4" data-testid="review-reviews">
      <TileLabel>
        <span id="rv-reviews-h">{t("review.reviews_title")}</span>
      </TileLabel>
      <p className="m-0 text-text-2" data-testid="review-resolution">
        {t(`review.resolution_${res.state}`, { submitted: res.submitted, needed: res.needed, round: res.round })}
      </p>
      {adjudicating ? (
        <p
          role="status"
          className="m-0 flex items-start gap-2 rounded-block border border-line-control px-4 py-3"
          data-testid="review-adjudication"
        >
          <Gavel aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
          {t("review.adjudication_you")}
        </p>
      ) : null}
      {waitingThird ? (
        <p
          role="status"
          className="m-0 flex items-start gap-2 rounded-block border border-line-control px-4 py-3"
          data-testid="review-adjudication"
        >
          <Gavel aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
          {t("review.adjudication_waiting")}
        </p>
      ) : null}
      {item.hidden_reviews > 0 ? (
        <p role="status" className="m-0 flex items-start gap-2 text-text-2" data-testid="review-hidden">
          <Lock aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
          {t("review.hidden_reviews", { n: item.hidden_reviews })}
        </p>
      ) : null}
      {rounds.length ? (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {rounds.map((r) => (
            <Entry key={r.id} r={r} />
          ))}
        </ul>
      ) : (
        <p className="m-0 text-text-3" data-testid="review-no-reviews">
          {t("review.no_reviews")}
        </p>
      )}
    </Tile>
  );
}
