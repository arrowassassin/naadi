import type { ReviewItemDetail } from "@naadi/shared";
import { Button, Tile, TileLabel } from "@naadi/ui";
import { Check, Loader2, ShieldAlert, X } from "lucide-react";
import { requestIdOf, reviewErrorKey, useRunChecks } from "./api";
import { useReviewCopy } from "./copy";
import { blockerKey } from "./logic";

const GATES = [
  { key: "lint_ok", name: "lint", blocker: "lint_errors" },
  { key: "source_ok", name: "source", blocker: "source_check_failed" },
  { key: "cross_check_ok", name: "cross", blocker: "model_cross_check_failed" },
  { key: "independent", name: "independent", blocker: "review_not_independent" },
] as const;

/** The four publish gates, each passed or failed in words, what blocks and what to do, and the buttons that re-run checks. */
export function GatesPanel({ item, canRun }: { item: ReviewItemDetail; canRun: boolean }) {
  const t = useReviewCopy();
  const run = useRunChecks(item.id);
  const err = run.error;
  return (
    <Tile as="section" aria-labelledby="rv-gates-h" className="flex flex-col gap-4" data-testid="review-gates">
      <TileLabel>
        <span id="rv-gates-h">{t("review.gates_title")}</span>
      </TileLabel>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {GATES.map((g) => {
          const ok = item.gates[g.key];
          return (
            <li
              key={g.key}
              data-gate={g.name}
              data-passed={ok}
              className="flex items-start gap-3 rounded-block border border-line-soft bg-surface-2 px-4 py-3"
            >
              {ok ? (
                <Check aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
              ) : (
                <X aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
              )}
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="font-semibold">
                  {t(`review.gate_${g.name}`)}
                  <span className="text-text-2"> · </span>
                  <span className={ok ? "" : "text-amber"}>
                    {ok ? t("review.gate_passed") : t("review.gate_failed")}
                  </span>
                </span>
                {ok ? null : <span className="text-text-2 text-ui-sm">{t(blockerKey(g.blocker))}</span>}
              </div>
            </li>
          );
        })}
      </ul>
      {item.gates.blockers.length > 0 ? (
        <p className="m-0 flex items-start gap-2 text-text-2 text-ui-sm" data-testid="review-blocked">
          <ShieldAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0 text-amber" />
          {t("review.gates_blocked")}
        </p>
      ) : (
        <p className="m-0 text-text-2 text-ui-sm" data-testid="review-all-clear">
          {t("review.gates_clear")}
        </p>
      )}
      {canRun ? (
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" disabled={run.isPending} onClick={() => run.mutate(false)}>
            {t("review.run_checks")}
          </Button>
          <Button
            variant="assistant"
            disabled={run.isPending}
            onClick={() => run.mutate(true)}
            icon={run.isPending && run.variables ? <Loader2 size={16} className="animate-spin" /> : undefined}
          >
            {t("review.run_solver")}
          </Button>
        </div>
      ) : null}
      {run.isPending ? (
        <p role="status" className="m-0 text-text-2 text-ui-sm">
          {run.variables ? t("review.running_solver") : t("review.running_checks")}
        </p>
      ) : null}
      {err ? (
        <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text">
          {t(reviewErrorKey(err))}
          {requestIdOf(err) ? (
            <span className="block font-mono text-label-sm text-text-3">{requestIdOf(err)}</span>
          ) : null}
        </p>
      ) : null}
    </Tile>
  );
}
