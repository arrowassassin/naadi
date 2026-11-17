import { cx } from "@naadi/ui";
import { Link, useRouterState } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMe } from "../../auth/api";
import { AdminPage, Failure, Loading, PermissionState, PILL, PILL_OFF, PILL_ON, useT } from "../components/ui";
import "../admin.css";

const NAV = [
  { to: "/admin", key: "admin.nav.overview", exact: true },
  { to: "/admin/chapters", key: "admin.nav.chapters" },
  { to: "/admin/media", key: "admin.nav.media" },
  { to: "/admin/questions", key: "admin.nav.questions" },
  { to: "/admin/queues", key: "admin.nav.queues" },
  { to: "/admin/reports", key: "admin.nav.reports" },
  { to: "/admin/coverage", key: "admin.nav.coverage" },
  { to: "/admin/config", key: "admin.nav.config" },
  { to: "/admin/agent-ops", key: "admin.nav.agent_ops" },
] as const;

function current(to: string, pathname: string, exact?: boolean): boolean {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (exact) return p === to;
  return p === to || p.startsWith(`${to}/`) || (to === "/admin/chapters" && p.startsWith("/admin/sections/"));
}

/** Console navigation: one row of pills (wrapping, never truncated). The page's own lime CTA stays the only lime. */
export function AdminNav() {
  const t = useT();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav aria-label={t("admin.nav.label")} data-admin-nav="">
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
        {NAV.map((n) => {
          const on = current(n.to, pathname, "exact" in n ? n.exact : false);
          return (
            <li key={n.to}>
              <Link to={n.to} aria-current={on ? "page" : undefined} className={cx(PILL, on ? PILL_ON : PILL_OFF)}>
                {t(n.key)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/**
 * The admin gate. The server is the authority (every /api/admin route is require_admin); this only decides what
 * to render: a non-admin sees the permission state and no admin request is made for them.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const t = useT();
  const me = useMe();
  if (me.isPending && !me.data) return <Loading rows={2} tall />;
  if (!me.data) {
    return (
      <AdminPage label={t("admin.label")} title={t("admin.title")}>
        <Failure error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </AdminPage>
    );
  }
  if (!me.data.is_admin) {
    return (
      <AdminPage label={t("admin.label")} title={t("admin.title")}>
        <PermissionState />
      </AdminPage>
    );
  }
  return (
    <div data-admin-console="" className="flex min-w-0 flex-col gap-6">
      <AdminNav />
      {children}
    </div>
  );
}
