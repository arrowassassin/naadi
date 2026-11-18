import type { ReviewItemDetail } from "@naadi/shared";
import { Button, Chip, Tile, TileLabel } from "@naadi/ui";
import { requestIdOf, reviewErrorKey, useActMinN, usePsychAction } from "./api";
import { useReviewCopy } from "./copy";

function num(v: number | null, digits: number, empty: string): string {
  return v === null ? empty : v.toFixed(digits);
}

/** Statistics of a published item (first attempts, N responses) with flags in words and Keep / Revise / Retire. */
export function PsychometricsPanel({ item }: { item: ReviewItemDetail }) {
  const t = useReviewCopy();
  const act = usePsychAction(item.id);
  const st = item.stats;
  const needed = useActMinN(st !== null && !st.can_act);
  if (!st) {
    return (
      <Tile as="section" aria-labelledby="rv-psy-h" className="flex flex-col gap-3" data-testid="review-psych">
        <TileLabel>
          <span id="rv-psy-h">{t("review.psych_title")}</span>
        </TileLabel>
        <p className="m-0 text-text-2">{t("review.psych_none")}</p>
      </Tile>
    );
  }
  const total = Object.values(st.option_counts).reduce((a, b) => a + b, 0);
  const flagged = st.flags.length > 0;
  const canActOn = item.status === "published" && flagged;
  const retireReason = !canActOn
    ? t("review.psych_not_flagged")
    : !st.can_act
      ? t("review.psych_retire_blocked", { n: st.n, needed: needed.data ?? "?" })
      : null;
  return (
    <Tile as="section" aria-labelledby="rv-psy-h" className="flex flex-col gap-5" data-testid="review-psych">
      <TileLabel>
        <span id="rv-psy-h">{t("review.psych_title")}</span>
      </TileLabel>
      <dl className="m-0 grid grid-cols-2 gap-x-6 gap-y-3 tablet:grid-cols-4">
        <div>
          <dt className="font-mono text-label text-text-3 uppercase">{t("review.psych_n")}</dt>
          <dd className="m-0 font-display font-bold text-[1.5rem]" data-testid="psych-n">
            {st.n}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-label text-text-3 uppercase">{t("review.psych_correct")}</dt>
          <dd className="m-0 font-display font-bold text-[1.5rem]">
            {st.p_correct === null ? t("review.psych_na") : `${Math.round(st.p_correct * 100)}%`}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-label text-text-3 uppercase">{t("review.psych_pb")}</dt>
          <dd className="m-0 font-display font-bold text-[1.5rem]">
            {num(st.point_biserial, 2, t("review.psych_na"))}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-label text-text-3 uppercase">{t("review.psych_median")}</dt>
          <dd className="m-0 font-display font-bold text-[1.5rem]">
            {st.median_seconds === null
              ? t("review.psych_na")
              : t("review.psych_seconds", { n: Math.round(st.median_seconds) })}
          </dd>
        </div>
      </dl>
      <p className="m-0 text-text-2">{t("review.psych_reports", { n: st.report_count })}</p>
      {Object.keys(st.option_counts).length ? (
        <table className="w-full border-collapse text-ui-sm" data-testid="psych-options">
          <caption className="mb-2 text-left font-mono text-label text-text-3 uppercase">
            {t("review.psych_options")}
          </caption>
          <thead>
            <tr className="text-left text-text-3">
              <th scope="col" className="py-2 pr-3 font-normal">
                {t("review.psych_option")}
              </th>
              <th scope="col" className="w-full py-2 pr-3 font-normal">
                <span className="sr-only">{t("review.psych_share")}</span>
              </th>
              <th scope="col" className="py-2 text-right font-normal">
                {t("review.psych_chosen")}
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(st.option_counts)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([label, count]) => {
                const share = total ? Math.round((100 * count) / total) : 0;
                return (
                  <tr key={label} className="border-line-soft border-t">
                    <th scope="row" className="py-2 pr-3 text-left font-mono font-normal">
                      {label}
                      {label === item.answer ? <span className="ml-2 text-text-3">{t("review.key")}</span> : null}
                    </th>
                    <td className="py-2 pr-3">
                      <span aria-hidden="true" className="block h-2 rounded-[4px] bg-line">
                        <span className="block h-2 rounded-[4px] bg-text-2" style={{ width: `${share}%` }} />
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono whitespace-nowrap">
                      {count} · {share}%
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      ) : null}
      <div className="flex flex-col gap-2">
        <h4 className="m-0 font-mono text-label text-text-3 uppercase">{t("review.psych_flags")}</h4>
        {flagged ? (
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0" data-testid="psych-flags">
            {st.flags.map((f) => (
              <li key={f}>
                <Chip tone="warning">{t(`review.psych_flag.${f}`)}</Chip>
              </li>
            ))}
          </ul>
        ) : (
          <p className="m-0 text-text-2">{t("review.psych_no_flags")}</p>
        )}
      </div>
      {!st.can_act ? (
        <p className="m-0 text-text-2" data-testid="psych-needed">
          {t("review.psych_needed", { needed: needed.data ?? "?", n: st.n })}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" disabled={!canActOn || act.isPending} onClick={() => act.mutate("keep")}>
          {t("review.psych_keep")}
        </Button>
        <Button variant="secondary" disabled={!canActOn || act.isPending} onClick={() => act.mutate("revise")}>
          {t("review.psych_revise")}
        </Button>
        <Button
          variant="secondary"
          disabled={retireReason !== null || act.isPending}
          onClick={() => act.mutate("retire")}
        >
          {t("review.psych_retire")}
        </Button>
      </div>
      {retireReason ? (
        <p className="m-0 text-text-2 text-ui-sm" data-testid="psych-retire-reason">
          {retireReason}
        </p>
      ) : null}
      {act.isSuccess ? (
        <p role="status" className="m-0 text-text-2 text-ui-sm">
          {t("review.psych_done")}
        </p>
      ) : null}
      {act.error ? (
        <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3">
          {t(reviewErrorKey(act.error))}
          {requestIdOf(act.error) ? (
            <span className="block font-mono text-label-sm text-text-3">{requestIdOf(act.error)}</span>
          ) : null}
        </p>
      ) : null}
    </Tile>
  );
}
