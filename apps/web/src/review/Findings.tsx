import type { ReviewFinding, ReviewItemDetail } from "@naadi/shared";
import { Tile, TileLabel } from "@naadi/ui";
import { Bot, CircleAlert, Info, TriangleAlert } from "lucide-react";
import { useReviewCopy } from "./copy";
import { findingVars, lintKey } from "./rules";

const SEVERITIES = ["error", "warn", "info"] as const;

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function Provenance({ item }: { item: ReviewItemDetail }) {
  const t = useReviewCopy();
  const a = item.author;
  return (
    <Tile as="section" variant="assistant" aria-labelledby="rv-prov-h" className="flex flex-col gap-3">
      <TileLabel variant="assistant">
        <span id="rv-prov-h" className="inline-flex items-center gap-2">
          <Bot aria-hidden="true" size={14} />
          {t("review.provenance_title")}
        </span>
      </TileLabel>
      {a ? (
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-ui-sm">
          <dt className="text-violet-text">{t("review.prov_model")}</dt>
          <dd className="m-0 break-all font-mono">{a.model ?? t("review.unknown")}</dd>
          <dt className="text-violet-text">{t("review.prov_prompt")}</dt>
          <dd className="m-0 break-all font-mono">{a.prompt_hash ?? t("review.unknown")}</dd>
          <dt className="text-violet-text">{t("review.prov_run")}</dt>
          <dd className="m-0 break-all font-mono">{a.run_id ?? t("review.unknown")}</dd>
          <dt className="text-violet-text">{t("review.prov_excerpts")}</dt>
          <dd className="m-0 break-all font-mono">
            {a.excerpt_ids.length ? a.excerpt_ids.join(", ") : t("review.prov_no_excerpts")}
          </dd>
        </dl>
      ) : (
        <p className="m-0 text-text-2">{t("review.prov_none")}</p>
      )}
    </Tile>
  );
}

function SeverityIcon({ severity }: { severity: ReviewFinding["severity"] }) {
  const Icon = severity === "error" ? CircleAlert : severity === "warn" ? TriangleAlert : Info;
  return <Icon aria-hidden="true" size={16} className={severity === "info" ? "text-text-3" : "text-amber"} />;
}

export function FindingList({ findings, testId }: { findings: ReviewFinding[]; testId?: string }) {
  const t = useReviewCopy();
  if (findings.length === 0) return null;
  return (
    <div className="flex flex-col gap-4" data-testid={testId}>
      {SEVERITIES.map((sev) => {
        const group = findings.filter((f) => f.severity === sev);
        if (group.length === 0) return null;
        return (
          <section key={sev} aria-label={t(`review.severity_${sev}`, { n: group.length })} data-severity={sev}>
            <h4 className="m-0 mb-2 flex items-center gap-2 font-mono text-label text-text-3 uppercase">
              <SeverityIcon severity={sev} />
              {t(`review.severity_${sev}`, { n: group.length })}
            </h4>
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {group.map((f, i) => (
                <li
                  // biome-ignore lint/suspicious/noArrayIndexKey: findings have no id; the list is static per load
                  key={`${f.rule}-${f.target}-${i}`}
                  className="flex items-start gap-3 rounded-block border border-line-soft bg-surface-2 px-4 py-3"
                >
                  <span className="shrink-0 font-mono text-label text-text-2">{f.rule}</span>
                  <span className="min-w-0 text-text-2">{t(lintKey(f.rule, f.severity), findingVars(f))}</span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function yesNo(t: ReturnType<typeof useReviewCopy>, v: boolean | null): string {
  return v === null ? t("review.not_run") : v ? t("review.yes") : t("review.no");
}

/** The blind solver's result in words and numbers; its model is the assistant's, so the block is violet. */
export function SolverBlock({ item }: { item: ReviewItemDetail }) {
  const t = useReviewCopy();
  const s = item.solver;
  return (
    <Tile as="section" variant="assistant" aria-labelledby="rv-solver-h" className="flex flex-col gap-4">
      <TileLabel variant="assistant">
        <span id="rv-solver-h">{t("review.solver_title")}</span>
      </TileLabel>
      {!s ? (
        <p className="m-0 text-text-2" data-testid="review-solver-none">
          {t("review.solver_none")}
        </p>
      ) : (
        <>
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-ui-sm" data-testid="review-solver">
            <dt className="text-violet-text">{t("review.solver_choice")}</dt>
            <dd className="m-0">
              {s.choice
                ? t(s.agrees ? "review.solver_agrees" : "review.solver_disagrees", {
                    choice: s.choice,
                    key: item.answer,
                  })
                : t("review.not_run")}
            </dd>
            <dt className="text-violet-text">{t("review.solver_confidence")}</dt>
            <dd className="m-0">{s.confidence === null ? t("review.not_run") : pct(s.confidence)}</dd>
            <dt className="text-violet-text">{t("review.solver_cover")}</dt>
            <dd className="m-0">
              {s.cover_matches === null
                ? t("review.not_run")
                : s.cover_matches
                  ? t("review.solver_cover_match")
                  : t("review.solver_cover_mismatch")}
            </dd>
            <dt className="text-violet-text">{t("review.solver_independent")}</dt>
            <dd className="m-0">{yesNo(t, s.independent)}</dd>
            <dt className="text-violet-text">{t("review.prov_model")}</dt>
            <dd className="m-0 break-all font-mono">{s.model ?? t("review.unknown")}</dd>
          </dl>
          {s.error ? (
            <p role="status" className="m-0 text-amber">
              {t("review.solver_error", { code: s.error })}
            </p>
          ) : null}
          {Object.keys(s.defensibility).length ? (
            <div>
              <h4 className="m-0 mb-2 font-mono text-label text-violet-text uppercase">
                {t("review.solver_defensibility")}
              </h4>
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                {Object.entries(s.defensibility)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([label, v]) => (
                    <li key={label} className="flex items-center gap-3 text-ui-sm">
                      <span className="w-6 font-mono">{label}</span>
                      <span aria-hidden="true" className="h-1.5 flex-1 rounded-[3px] bg-line">
                        <span
                          className="block h-1.5 rounded-[3px] bg-violet"
                          style={{ width: `${Math.round(v * 100)}%` }}
                        />
                      </span>
                      <span className="w-12 text-right font-mono">{pct(v)}</span>
                      <span className="w-28 shrink-0 text-text-2">
                        {v > 0.5 ? t("review.defensible_yes") : t("review.defensible_no")}
                      </span>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </>
      )}
      <FindingList findings={item.solver_findings} testId="review-solver-findings" />
    </Tile>
  );
}
