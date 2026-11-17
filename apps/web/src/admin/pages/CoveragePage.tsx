import { cx, Tile } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { CircleCheck, CircleDashed, CircleHelp, type LucideIcon, TriangleAlert } from "lucide-react";
import { useMemo } from "react";
import { api, COVERAGE_COUNTS, type Coverage, type CoverageStatus, EXAMS, qk } from "../api";
import { AdminPage, Empty, Failure, FilterPills, fmtNumber, Loading, useT } from "../components/ui";
import type { ExamSearch } from "../routes";
import { EXAM_KEYS } from "./ChaptersPage";

type Counts = Partial<Record<(typeof COVERAGE_COUNTS)[number], number>>;

export const COVERAGE_LOOK: Record<CoverageStatus, { key: string; means: string; Icon: LucideIcon; cell: string }> = {
  gap: {
    key: "admin.coverage.gap",
    means: "admin.coverage.gap_means",
    Icon: TriangleAlert,
    cell: "admin-cell-gap border-amber/60",
  },
  thin: {
    key: "admin.coverage.thin",
    means: "admin.coverage.thin_means",
    Icon: CircleDashed,
    cell: "admin-cell-thin border-line-strong",
  },
  covered: {
    key: "admin.coverage.covered",
    means: "admin.coverage.covered_means",
    Icon: CircleCheck,
    cell: "border-line-soft bg-surface-2",
  },
};
const COUNT_KEYS: Record<string, string> = {
  sections: "admin.coverage.count.sections",
  videos: "admin.coverage.count.videos",
  questions: "admin.coverage.count.questions",
  cards: "admin.coverage.count.cards",
};
const COUNT_SHORT: Record<string, string> = {
  sections: "admin.coverage.short.sections",
  videos: "admin.coverage.short.videos",
  questions: "admin.coverage.short.questions",
  cards: "admin.coverage.short.cards",
};

function StatusWord({ status }: { status: CoverageStatus }) {
  const t = useT();
  const look = COVERAGE_LOOK[status];
  if (!look) {
    return (
      <span className="inline-flex items-center gap-1.5 text-ui-sm">
        <CircleHelp aria-hidden="true" size={14} />
        {status}
      </span>
    );
  }
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-ui-sm font-semibold", status === "gap" && "text-amber")}>
      <look.Icon aria-hidden="true" size={14} />
      {t(look.key)}
    </span>
  );
}

function CountsLine({ counts }: { counts: Counts }) {
  const t = useT();
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-label-sm text-text-2">
      {COVERAGE_COUNTS.map((k) => (
        <span key={k} title={t(COUNT_KEYS[k] ?? k)}>
          <span className="sr-only">{t(COUNT_KEYS[k] ?? k)} </span>
          <span aria-hidden="true">{t(COUNT_SHORT[k] ?? k)}</span> {fmtNumber(counts[k] ?? 0)}
        </span>
      ))}
    </span>
  );
}

function HeatMap({ data }: { data: Coverage }) {
  const t = useT();
  const cell = useMemo(() => {
    const m = new Map<string, Coverage["cells"][number]>();
    for (const c of data.cells) m.set(`${c.system_id}|${c.discipline_id}`, c);
    return m;
  }, [data.cells]);
  return (
    <section
      aria-label={t("admin.coverage.grid_label")}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
      tabIndex={0}
      className="relative overflow-x-auto rounded-block border border-line-soft [-webkit-overflow-scrolling:touch]"
    >
      <table className="w-full min-w-[40rem] border-collapse text-left" data-testid="coverage-grid">
        <caption className="sr-only">{t("admin.coverage.grid_label")}</caption>
        <thead>
          <tr>
            <th scope="col" className="sticky left-0 bg-surface-1 p-3 font-mono text-label-sm text-text-3 uppercase">
              {t("admin.coverage.system")}
            </th>
            {data.disciplines.map((d) => (
              <th key={d.id} scope="col" className="p-3 align-bottom font-mono text-label-sm text-text-3 uppercase">
                {d.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.systems.map((s) => (
            <tr key={s.id} className="border-line-soft border-t">
              <th scope="row" className="sticky left-0 bg-surface-1 p-3 text-ui-sm font-semibold text-text">
                {s.label}
              </th>
              {data.disciplines.map((d) => {
                const c = cell.get(`${s.id}|${d.id}`);
                if (!c) {
                  return (
                    <td key={d.id} className="p-1.5">
                      <span className="block p-2 text-ui-sm text-text-3">{t("admin.coverage.none")}</span>
                    </td>
                  );
                }
                return (
                  <td key={d.id} className="p-1.5 align-top">
                    <div
                      data-cell={`${s.id}|${d.id}`}
                      data-status={c.status}
                      className={cx(
                        "flex min-w-28 flex-col gap-1 rounded-inner border p-2",
                        COVERAGE_LOOK[c.status]?.cell,
                      )}
                    >
                      <StatusWord status={c.status} />
                      <CountsLine counts={c.counts} />
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** `/admin/coverage`: system × discipline heat-map against the §7.2 goals, per-system totals and every topic. */
export function CoveragePage({ search }: { search: ExamSearch }) {
  const t = useT();
  const navigate = useNavigate();
  const exam = search.exam ?? "step1";
  const q = useQuery({ queryKey: qk.coverage(exam), queryFn: ({ signal }) => api.coverage(exam, signal) });
  return (
    <AdminPage label={t("admin.label")} title={t("admin.coverage.title")}>
      <FilterPills
        label={t("admin.field.exam")}
        value={exam}
        options={EXAMS.map((x) => ({ value: x, label: t(EXAM_KEYS[x] ?? x) }))}
        onChange={(v) => void navigate({ to: "/admin/coverage", search: { exam: v }, replace: true })}
      />
      {q.isPending ? (
        <Loading rows={3} tall />
      ) : q.isError ? (
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.systems.length === 0 ? (
        <Empty title={t("admin.coverage.empty")} body={t("admin.coverage.empty_body")} />
      ) : (
        <>
          <p className="m-0 text-text-2">
            {t("admin.coverage.goals", {
              sections: String(q.data.goals.sections ?? 0),
              videos: String(q.data.goals.videos ?? 0),
              questions: String(q.data.goals.questions ?? 0),
              cards: String(q.data.goals.cards ?? 0),
            })}
          </p>
          <ul className="m-0 flex list-none flex-wrap gap-4 p-0" aria-label={t("admin.coverage.legend")}>
            {(Object.keys(COVERAGE_LOOK) as CoverageStatus[]).map((s) => (
              <li
                key={s}
                className={cx("flex items-center gap-2 rounded-inner border px-3 py-2", COVERAGE_LOOK[s].cell)}
              >
                <StatusWord status={s} />
                <span className="text-ui-sm text-text-2">{t(COVERAGE_LOOK[s].means)}</span>
              </li>
            ))}
          </ul>
          <HeatMap data={q.data} />
          {q.data.system_totals.length ? (
            <Tile as="section" aria-labelledby="sys-h" className="flex flex-col gap-4">
              <h2 id="sys-h" className="m-0 font-display font-bold text-[1.5rem]">
                {t("admin.coverage.by_system")}
              </h2>
              <ul className="m-0 grid list-none gap-4 p-0 tablet:grid-cols-2">
                {q.data.system_totals.map((s) => {
                  const pct = s.topics ? Math.round((100 * s.topics_covered) / s.topics) : 0;
                  return (
                    <li key={s.id} className="flex flex-col gap-1.5">
                      <div className="flex justify-between gap-3 text-ui-sm">
                        <span>{s.label}</span>
                        <span className="font-mono text-text-2">
                          {t("admin.coverage.topics_covered", {
                            covered: String(s.topics_covered),
                            total: String(s.topics),
                          })}
                        </span>
                      </div>
                      <progress
                        className="admin-bar h-1.5 w-full"
                        max={100}
                        value={pct}
                        aria-label={t("admin.coverage.topics_covered", {
                          covered: String(s.topics_covered),
                          total: String(s.topics),
                        })}
                      />
                    </li>
                  );
                })}
              </ul>
            </Tile>
          ) : null}
          <Tile as="section" aria-labelledby="topics-h" className="flex min-w-0 flex-col gap-4">
            <h2 id="topics-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("admin.coverage.topics")}
            </h2>
            {q.data.topics.length === 0 ? (
              <Empty title={t("admin.coverage.topics_empty")} />
            ) : (
              <section
                aria-label={t("admin.coverage.topics")}
                // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
                tabIndex={0}
                className="relative overflow-x-auto"
              >
                <table className="w-full min-w-[36rem] border-collapse text-left text-ui-sm">
                  <thead>
                    <tr className="font-mono text-label-sm text-text-3 uppercase">
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.coverage.topic")}
                      </th>
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.field.status")}
                      </th>
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.coverage.counts")}
                      </th>
                      <th scope="col" className="py-2">
                        {t("admin.coverage.missing")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.topics.map((tp) => (
                      <tr key={tp.id} className="border-line-soft border-t" data-topic={tp.id}>
                        <th scope="row" className="py-2.5 pr-3 font-normal text-text">
                          {tp.label}
                        </th>
                        <td className="py-2.5 pr-3">
                          <StatusWord status={tp.status} />
                        </td>
                        <td className="py-2.5 pr-3">
                          <CountsLine counts={tp.counts} />
                        </td>
                        <td className="py-2.5 text-text-2">
                          {tp.missing.length
                            ? tp.missing.map((m) => t(COUNT_KEYS[m] ?? m)).join(", ")
                            : t("admin.coverage.nothing_missing")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </Tile>
        </>
      )}
    </AdminPage>
  );
}
