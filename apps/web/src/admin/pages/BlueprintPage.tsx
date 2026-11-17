import { cx, Tile } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { CircleCheck, CircleDashed, CircleHelp, type LucideIcon, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { api, type BlueprintArea, EXAMS, qk } from "../api";
import { AdminPage, Empty, Failure, FilterPills, fmtNumber, Loading, useT } from "../components/ui";
import type { ExamSearch } from "../routes";
import { EXAM_KEYS } from "./ChaptersPage";

type AreaStatus = BlueprintArea["status"];
type Filter = "all" | AreaStatus;

/** Status is a word plus an icon, never a colour alone. */
const LOOK: Record<AreaStatus, { key: string; means: string; Icon: LucideIcon; cell: string }> = {
  below_min: {
    key: "admin.blueprint.below_min",
    means: "admin.blueprint.below_min_means",
    Icon: TriangleAlert,
    cell: "admin-cell-gap border-amber/60",
  },
  below_target: {
    key: "admin.blueprint.below_target",
    means: "admin.blueprint.below_target_means",
    Icon: CircleDashed,
    cell: "admin-cell-thin border-line-strong",
  },
  met: {
    key: "admin.blueprint.met",
    means: "admin.blueprint.met_means",
    Icon: CircleCheck,
    cell: "border-line-soft bg-surface-2",
  },
};
const STATUSES = Object.keys(LOOK) as AreaStatus[];
const lookOf = (s: AreaStatus) => LOOK[s] as (typeof LOOK)[AreaStatus];

function StatusWord({ status }: { status: AreaStatus }) {
  const t = useT();
  const look = LOOK[status];
  if (!look) {
    return (
      <span className="inline-flex items-center gap-1.5 text-ui-sm">
        <CircleHelp aria-hidden="true" size={14} />
        {status}
      </span>
    );
  }
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-ui-sm font-semibold",
        status === "below_min" && "text-amber",
      )}
    >
      <look.Icon aria-hidden="true" size={14} />
      {t(look.key)}
    </span>
  );
}

/** `/admin/blueprint`: published items per USMLE-2026 leaf area against the blueprint minimum and target. */
export function BlueprintPage({ search }: { search: ExamSearch }) {
  const t = useT();
  const navigate = useNavigate();
  const exam = search.exam ?? "step1";
  const [filter, setFilter] = useState<Filter>("all");
  const q = useQuery({ queryKey: qk.blueprint(exam), queryFn: ({ signal }) => api.blueprint(exam, signal) });
  const areas = q.data ? q.data.area_rows.filter((a) => filter === "all" || a.status === filter) : [];
  const systemLabel = new Map((q.data?.systems ?? []).map((s) => [s.code, s.label]));
  return (
    <AdminPage label={t("admin.label")} title={t("admin.blueprint.title")}>
      <FilterPills
        label={t("admin.field.exam")}
        value={exam}
        options={EXAMS.map((x) => ({ value: x, label: t(EXAM_KEYS[x] ?? x) }))}
        onChange={(v) => void navigate({ to: "/admin/blueprint", search: { exam: v }, replace: true })}
      />
      {q.isPending ? (
        <Loading rows={3} tall />
      ) : q.isError ? (
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.areas === 0 ? (
        <Empty title={t("admin.blueprint.empty")} body={t("admin.blueprint.empty_body")} />
      ) : (
        <>
          <p className="m-0 text-text-2" data-testid="blueprint-summary">
            {t("admin.blueprint.summary", {
              items: fmtNumber(q.data.items),
              target: fmtNumber(q.data.target_items),
              areas: fmtNumber(q.data.areas),
              below: fmtNumber(q.data.areas_below_min),
              met: fmtNumber(q.data.areas_met),
            })}
          </p>
          <p className="m-0 text-ui-sm text-text-3">{t("admin.blueprint.source_note")}</p>
          <ul className="m-0 flex list-none flex-wrap gap-4 p-0" aria-label={t("admin.blueprint.legend")}>
            {STATUSES.map((s) => (
              <li key={s} className={cx("flex items-center gap-2 rounded-inner border px-3 py-2", lookOf(s).cell)}>
                <StatusWord status={s} />
                <span className="text-ui-sm text-text-2">{t(lookOf(s).means)}</span>
              </li>
            ))}
          </ul>
          <Tile as="section" aria-labelledby="bp-sys-h" className="flex min-w-0 flex-col gap-4">
            <h2 id="bp-sys-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("admin.blueprint.by_system")}
            </h2>
            <ul className="m-0 grid list-none gap-4 p-0 tablet:grid-cols-2" data-testid="blueprint-systems">
              {q.data.systems.map((s) => {
                const pct = s.areas ? Math.round((100 * s.areas_at_min) / s.areas) : 0;
                const atMin = t("admin.blueprint.areas_at_min", {
                  done: fmtNumber(s.areas_at_min),
                  total: fmtNumber(s.areas),
                });
                return (
                  <li key={s.code} className="flex min-w-0 flex-col gap-1.5" data-system={s.code}>
                    <div className="flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-ui-sm">
                      <span className="min-w-0">
                        <span className="font-mono text-text-3">{s.code}</span> {s.label}
                      </span>
                      <span className="font-mono text-text-2">
                        {t("admin.blueprint.items_of", {
                          items: fmtNumber(s.items),
                          target: fmtNumber(s.target_items),
                        })}
                      </span>
                    </div>
                    <progress className="admin-bar h-1.5 w-full" max={100} value={pct} aria-label={atMin} />
                    <span className="text-label-sm text-text-2">{atMin}</span>
                  </li>
                );
              })}
            </ul>
          </Tile>
          <Tile as="section" aria-labelledby="bp-areas-h" className="flex min-w-0 flex-col gap-4">
            <h2 id="bp-areas-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("admin.blueprint.areas")}
            </h2>
            <FilterPills
              label={t("admin.blueprint.filter")}
              value={filter}
              options={[
                { value: "all" as Filter, label: t("admin.blueprint.filter_all") },
                ...STATUSES.map((s) => ({ value: s as Filter, label: t(lookOf(s).key) })),
              ]}
              onChange={(v) => setFilter(v)}
            />
            {areas.length === 0 ? (
              <Empty title={t("admin.blueprint.none_match")} />
            ) : (
              <section
                aria-label={t("admin.blueprint.areas")}
                // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
                tabIndex={0}
                className="relative overflow-x-auto [-webkit-overflow-scrolling:touch]"
              >
                <table
                  className="w-full min-w-[40rem] border-collapse text-left text-ui-sm"
                  data-testid="blueprint-areas"
                >
                  <caption className="sr-only">{t("admin.blueprint.areas")}</caption>
                  <thead>
                    <tr className="font-mono text-label-sm text-text-3 uppercase">
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.blueprint.area")}
                      </th>
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.field.status")}
                      </th>
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.blueprint.items")}
                      </th>
                      <th scope="col" className="py-2 pr-3">
                        {t("admin.blueprint.sections")}
                      </th>
                      <th scope="col" className="py-2">
                        {t("admin.blueprint.cards")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {areas.map((a) => (
                      <tr key={a.id} className="border-line-soft border-t" data-area={a.id} data-status={a.status}>
                        <th scope="row" className="py-2.5 pr-3 font-normal text-text">
                          <span className="block font-mono text-label-sm text-text-3">
                            {a.code} · {systemLabel.get(a.system) ?? a.system}
                          </span>
                          {a.label}
                        </th>
                        <td className="py-2.5 pr-3">
                          <StatusWord status={a.status} />
                        </td>
                        <td className="py-2.5 pr-3 font-mono">
                          {t("admin.blueprint.items_min_target", {
                            items: fmtNumber(a.items),
                            min: fmtNumber(a.min_items),
                            target: fmtNumber(a.target_items),
                          })}
                        </td>
                        <td className="py-2.5 pr-3 font-mono">{fmtNumber(a.sections)}</td>
                        <td className="py-2.5 font-mono">{fmtNumber(a.cards)}</td>
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
