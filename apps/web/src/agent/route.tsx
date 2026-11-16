import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute } from "@tanstack/react-router";
import { lazy, Suspense } from "react";

const AgentDiagnostics = lazy(() => import("./AgentDiagnostics").then((m) => ({ default: m.AgentDiagnostics })));

function Page() {
  return (
    <Suspense fallback={<Skeleton className="mt-10 h-40 w-full" />}>
      <AgentDiagnostics />
    </Suspense>
  );
}

/** `/admin/agent` under the signed-in app layout. The SDK and page load only when an admin opens it. */
export function agentDiagnosticsRoute<P extends AnyRoute>(parent: P) {
  return createRoute({ getParentRoute: () => parent, path: "/admin/agent", component: Page });
}
