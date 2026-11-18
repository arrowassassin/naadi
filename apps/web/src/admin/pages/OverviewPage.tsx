import { Tile, TileLabel } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { api, qk } from "../api";
import { AdminPage, Failure, fmtNumber, Loading, useT } from "../components/ui";

const CTA =
  "inline-flex min-h-14 items-center gap-2 self-start rounded-pill bg-lime px-7 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink";

function Count({ n, pending }: { n: number | undefined; pending: boolean }) {
  const t = useT();
  if (pending) return <span className="font-display font-bold text-[2.5rem] text-text-3">…</span>;
  if (n === undefined) return <span className="text-text-3">{t("admin.overview.count_failed")}</span>;
  return <span className="font-display font-bold text-[2.5rem] leading-none tracking-[-0.03em]">{fmtNumber(n)}</span>;
}

/** `/admin`: what needs attention, with counts, and a link into every area. */
export function OverviewPage() {
  const t = useT();
  const overview = useQuery({ queryKey: qk.overview, queryFn: ({ signal }) => api.overview(signal) });
  const queues = useQuery({ queryKey: qk.queues, queryFn: ({ signal }) => api.queues(signal) });
  const reports = useQuery({ queryKey: qk.reports("open"), queryFn: ({ signal }) => api.reports("open", signal) });
  const chapters = useQuery({ queryKey: qk.chapters({}), queryFn: ({ signal }) => api.chapters({}, signal) });

  if (overview.isError) {
    return (
      <AdminPage label={t("admin.label")} title={t("admin.overview.title")}>
        <Failure error={overview.error} onRetry={() => void overview.refetch()} retrying={overview.isFetching} />
      </AdminPage>
    );
  }
  const pending = queues.data?.items.reduce((s, q) => s + q.count, 0);
  const tiles = [
    { to: "/admin/queues", key: "admin.overview.queues", n: pending, q: queues },
    { to: "/admin/reports", key: "admin.overview.reports", n: reports.data?.items.length, q: reports },
    { to: "/admin/chapters", key: "admin.overview.chapters", n: chapters.data?.items.length, q: chapters },
  ] as const;
  const links = [
    { to: "/admin/media", key: "admin.nav.media", body: "admin.overview.media_body" },
    { to: "/admin/questions", key: "admin.nav.questions", body: "admin.overview.questions_body" },
    { to: "/admin/coverage", key: "admin.nav.coverage", body: "admin.overview.coverage_body" },
    { to: "/admin/config", key: "admin.nav.config", body: "admin.overview.config_body" },
    { to: "/admin/agent-ops", key: "admin.nav.agent_ops", body: "admin.overview.agent_ops_body" },
    { to: "/admin/agent", key: "admin.nav.agent_diagnostics", body: "admin.overview.agent_diag_body" },
  ] as const;

  return (
    <AdminPage
      label={t("admin.label")}
      title={t("admin.overview.title")}
      actions={
        pending ? (
          <Link to="/admin/queues" className={CTA}>
            {t("admin.overview.review_queues", { n: String(pending) })}
            <ArrowRight aria-hidden="true" size={18} />
          </Link>
        ) : null
      }
    >
      {overview.data ? (
        <p className="m-0 font-mono text-label-sm text-text-3 uppercase">
          {t("admin.overview.env", {
            env: overview.data.environment,
            version: overview.data.version,
            admins: String(overview.data.admins_configured),
            profiles: fmtNumber(overview.data.profiles),
          })}
        </p>
      ) : (
        <Loading rows={1} />
      )}
      <div className="grid gap-4 tablet:grid-cols-3">
        {tiles.map((tile) => (
          <Tile key={tile.to} as="article" className="flex flex-col gap-3">
            <TileLabel>{t(tile.key)}</TileLabel>
            <Count n={tile.n} pending={tile.q.isPending} />
            <Link to={tile.to} className="inline-flex min-h-11 items-center gap-2 self-start text-text">
              {t("admin.overview.open")}
              <span className="sr-only">{t(tile.key)}</span>
              <ArrowRight aria-hidden="true" size={16} />
            </Link>
          </Tile>
        ))}
      </div>
      <ul className="m-0 grid list-none gap-3 p-0 tablet:grid-cols-2 laptop:grid-cols-3">
        {links.map((l) => (
          <li key={l.to}>
            <Link
              to={l.to}
              className="flex min-h-full flex-col gap-1 rounded-block border border-line-soft bg-surface-1 p-5 text-text no-underline hover:border-line-control hover:text-text"
            >
              <span className="flex items-center justify-between gap-2 font-semibold">
                {t(l.key)}
                <ArrowRight aria-hidden="true" size={16} />
              </span>
              <span className="text-ui-sm text-text-2">{t(l.body)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </AdminPage>
  );
}
