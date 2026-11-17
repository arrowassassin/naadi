import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import { lazyPage } from "../shell/lazyPage";

const PathwayPage = lazyPage(() => import("./PathwayPage").then((m) => m.PathwayPage));

/** `/pathway` while the server's `features.pathway` flag is on (offline with a saved profile: the device copy). */
function PathwayGate() {
  const me = useMe();
  if (!me.data) {
    return me.isError ? (
      <PathwayPage />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (me.data.features.pathway === false) return <Navigate to="/" replace />;
  return <PathwayPage />;
}

/** The Pathway route under the protected app layout; `router.tsx` adds `...pathwayRoutes(appRoute)`. */
export function pathwayRoutes(parent: AnyRoute) {
  const pathway = createRoute({ getParentRoute: () => parent, path: "/pathway", component: PathwayGate });
  return [pathway] as const;
}
