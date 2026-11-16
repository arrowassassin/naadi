import { createRootRoute, createRoute, createRouter, Link, Outlet } from "@tanstack/react-router";
import { SystemPage } from "./system/SystemPage";

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
    <main id="main" className="mx-auto flex max-w-(--content-max) flex-col gap-4 px-(--gutter) py-16">
      <h1 className="m-0 font-display text-headline">Page not found</h1>
      <p className="m-0 text-text-2">There is nothing at this address.</p>
      <p className="m-0">
        <Link to="/system">Open the design system</Link>
      </p>
    </main>
  );
}

const rootRoute = createRootRoute({ component: RootLayout, notFoundComponent: NotFound });

// `/` shows the token sheet until Today replaces it; `/system` stays as the design-system reference.
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: SystemPage });
const systemRoute = createRoute({ getParentRoute: () => rootRoute, path: "/system", component: SystemPage });

export const routeTree = rootRoute.addChildren([indexRoute, systemRoute]);

export const router = createRouter({ routeTree, defaultPreload: "intent", scrollRestoration: true });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
