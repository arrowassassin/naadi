import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import { lazyPage } from "../shell/lazyPage";

const PlannerPage = lazyPage(() => import("./PlannerPage").then((m) => m.PlannerPage));

/** `/plan`: only while the server's `features.planner` is on (the flag that also shows Plan in the navigation). */
function PlannerGate() {
  const me = useMe();
  if (!me.data) {
    // Offline with no saved profile: the last saved week still shows from this device.
    return me.isError ? (
      <PlannerPage />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (!me.data.features.planner) return <Navigate to="/" replace />;
  return <PlannerPage />;
}

/** One route under the protected app layout; `router.tsx` adds `plannerRoute(appRoute)`. */
export function plannerRoute(parent: AnyRoute) {
  return createRoute({ getParentRoute: () => parent, path: "/plan", component: PlannerGate });
}
