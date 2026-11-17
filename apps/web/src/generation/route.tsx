import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { useMe } from "../auth/api";

const AdminGenerated = lazy(() => import("./AdminGenerated").then((m) => ({ default: m.AdminGenerated })));
const GeneratedLibrary = lazy(() => import("./GeneratedLibrary").then((m) => ({ default: m.GeneratedLibrary })));

function AdminPage() {
  return (
    <Suspense fallback={<Skeleton className="mt-10 h-40 w-full" />}>
      <AdminGenerated />
    </Suspense>
  );
}

/** `/generated`: the student's generated media, only while `features.generation` is on (it stays off until the
 * generation token is set). S15 also mounts `GeneratedLibrary` as the Library's "Generated" filter. */
function GeneratedPage() {
  const me = useMe();
  if (!me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (!me.data.features.generation) return <Navigate to="/" replace />;
  return (
    <Suspense fallback={<Skeleton className="mt-10 h-40 w-full" />}>
      <GeneratedLibrary standalone />
    </Suspense>
  );
}

/** `/admin/generated` under the signed-in app layout (admins only: the page renders nothing for anyone else and
 * the API refuses non-admins). S19 moves this list into its approval queues. */
export function adminGeneratedRoute<P extends AnyRoute>(parent: P) {
  return createRoute({ getParentRoute: () => parent, path: "/admin/generated", component: AdminPage });
}

export function generatedRoute<P extends AnyRoute>(parent: P) {
  return createRoute({ getParentRoute: () => parent, path: "/generated", component: GeneratedPage });
}
