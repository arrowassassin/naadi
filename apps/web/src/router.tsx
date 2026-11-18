import { ErrorState } from "@naadi/ui";
import { createRootRoute, createRoute, createRouter, Link, Outlet, redirect } from "@tanstack/react-router";
import { adminRoutes } from "./admin/routes";
import { agentDiagnosticsRoute } from "./agent/route";
import { safeNext } from "./auth/redirect";
import { getAuthState, startAuth, useAuth } from "./auth/session";
import { cardsRoute } from "./cards/routes";
import { generatedRoute } from "./generation/route";
import { inkLabRoute } from "./ink/route";
import { learnRoutes } from "./learn/routes";
import { notebooksRoutes } from "./notebooks/routes";
import { notesRoutes } from "./notes/routes";
import { pathwayRoutes } from "./pathway/routes";
import { plannerRoute } from "./planner/routes";
import { practiceRoutes } from "./practice/routes";
import { adminReviewRoutes } from "./review/route";
import { TodayPage } from "./routes/TodayPage";
import { searchRoute } from "./search/routes";
import { AppShell } from "./shell/AppShell";
import { Wordmark } from "./shell/Logo";
import { lazyPage } from "./shell/lazyPage";
import { welcomeRoute } from "./today/routes";

// Pages other than Today load as their own chunks (segment 22 budget: initial JS <= 250 KB gzip).
const SettingsAssistantPage = lazyPage(() =>
  import("./assistant/SettingsAssistantPage").then((m) => m.SettingsAssistantPage),
);
const OfflineSettingsPage = lazyPage(() => import("./offline/OfflineSettingsPage").then((m) => m.OfflineSettingsPage));
const InkSettingsPage = lazyPage(() => import("./ink/persist/InkSettingsPage").then((m) => m.InkSettingsPage));
const AccountPage = lazyPage(() => import("./routes/AccountPage").then((m) => m.AccountPage));
const CallbackPage = lazyPage(() => import("./routes/CallbackPage").then((m) => m.CallbackPage));
const SignInPage = lazyPage(() => import("./routes/SignInPage").then((m) => m.SignInPage));
const AboutPage = lazyPage(() => import("./settings/AboutPage").then((m) => m.AboutPage));
const AppearancePage = lazyPage(() => import("./settings/AppearancePage").then((m) => m.AppearancePage));
const DataExportPage = lazyPage(() => import("./settings/DataExportPage").then((m) => m.DataExportPage));
const StudyPlanPage = lazyPage(() => import("./settings/StudyPlanPage").then((m) => m.StudyPlanPage));
const SystemPage = lazyPage(() => import("./system/SystemPage").then((m) => m.SystemPage));

function RootLayout() {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-pill focus:bg-paper focus:px-4 focus:py-3 focus:text-ground"
      >
        Skip to content
      </a>
      <Outlet />
    </>
  );
}

function NotFound() {
  return (
    <main id="main" tabIndex={-1} className="mx-auto flex max-w-(--content-max) flex-col gap-4 px-(--gutter) py-16">
      <h1 className="m-0 font-display text-headline">Page not found</h1>
      <p className="m-0 text-text-2">There is nothing at this address.</p>
      <p className="m-0">
        <Link to="/">Go to Today</Link>
      </p>
    </main>
  );
}

/** Protected layout: the shell, or a full-page error when sign-in cannot be checked at all. */
function AppGate() {
  const auth = useAuth();
  if (auth.status === "unavailable") {
    return (
      <main id="main" tabIndex={-1} className="mx-auto flex max-w-[40rem] flex-col gap-8 px-(--gutter) py-10">
        <Wordmark className="text-[1.375rem]" size={32} />
        <ErrorState
          title="Naadi could not start"
          message={auth.error.message}
          requestId={auth.error.requestId}
          onRetry={() => window.location.reload()}
          retryLabel="Reload"
        />
      </main>
    );
  }
  return <AppShell />;
}

const rootRoute = createRootRoute({ component: RootLayout, notFoundComponent: NotFound });

interface SignInSearch {
  redirect?: string;
  error?: "expired";
  signed_out?: 1;
}

const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sign-in",
  validateSearch: (s: Record<string, unknown>): SignInSearch => ({
    redirect: typeof s.redirect === "string" ? s.redirect : undefined,
    error: s.error === "expired" ? "expired" : undefined,
    signed_out: s.signed_out === 1 || s.signed_out === "1" ? 1 : undefined,
  }),
  beforeLoad: async ({ search }) => {
    await startAuth();
    if (getAuthState().status === "signed-in") throw redirect({ href: safeNext(search.redirect), replace: true });
  },
  component: function SignInRoute() {
    const search = signInRoute.useSearch();
    const notice = search.error === "expired" ? "expired" : search.signed_out ? "signed-out" : null;
    return <SignInPage next={safeNext(search.redirect)} notice={notice} />;
  },
});

const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  beforeLoad: async () => {
    await startAuth();
  },
  component: CallbackPage,
});

const systemRoute = createRoute({ getParentRoute: () => rootRoute, path: "/system", component: SystemPage });

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: async ({ location }) => {
    await startAuth();
    if (getAuthState().status === "signed-out") {
      throw redirect({ to: "/sign-in", search: { redirect: safeNext(location.href) }, replace: true });
    }
  },
  component: AppGate,
});

const todayRoute = createRoute({ getParentRoute: () => appRoute, path: "/", component: TodayPage });
const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings",
  beforeLoad: ({ location }) => {
    if (location.pathname === "/settings" || location.pathname === "/settings/") {
      throw redirect({ to: "/settings/account", replace: true });
    }
  },
  component: Outlet,
});
const accountRoute = createRoute({ getParentRoute: () => settingsRoute, path: "/account", component: AccountPage });
const inkSettingsRoute = createRoute({ getParentRoute: () => settingsRoute, path: "/ink", component: InkSettingsPage });
// Settings groups (S21): one route line per group; S18 (/offline) and S14 (/assistant) add theirs here and in groups.ts.
const planSettingsRoute = createRoute({ getParentRoute: () => settingsRoute, path: "/plan", component: StudyPlanPage });
const appearanceRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/appearance",
  component: AppearancePage,
});
const dataSettingsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/data",
  component: DataExportPage,
});
const aboutRoute = createRoute({ getParentRoute: () => settingsRoute, path: "/about", component: AboutPage });
const assistantSettingsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/assistant",
  component: SettingsAssistantPage,
});
const offlineSettingsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/offline",
  component: OfflineSettingsPage,
});

export const routeTree = rootRoute.addChildren([
  signInRoute,
  callbackRoute,
  systemRoute,
  inkLabRoute(rootRoute),
  welcomeRoute(rootRoute),
  appRoute.addChildren([
    // one route per line, so parallel segments add theirs without merge conflicts
    todayRoute,
    learnRoutes(appRoute),
    practiceRoutes(appRoute),
    cardsRoute(appRoute),
    plannerRoute(appRoute),
    searchRoute(appRoute),
    notebooksRoutes(appRoute),
    ...notesRoutes(appRoute),
    ...pathwayRoutes(appRoute),
    settingsRoute.addChildren([
      accountRoute,
      inkSettingsRoute,
      planSettingsRoute,
      appearanceRoute,
      dataSettingsRoute,
      aboutRoute,
      assistantSettingsRoute,
      offlineSettingsRoute,
    ]),
    agentDiagnosticsRoute(appRoute),
    ...adminReviewRoutes(appRoute),
    generatedRoute(appRoute),
    ...adminRoutes(appRoute),
  ]),
]);

export const router = createRouter({ routeTree, defaultPreload: "intent", scrollRestoration: true });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
