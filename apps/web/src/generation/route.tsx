import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { Suspense } from "react";
import { useMe } from "../auth/api";
import { lazyWithFallback } from "../shell/lazyPage";

const GeneratedLibrary = lazyWithFallback(() => import("./GeneratedLibrary").then((m) => m.GeneratedLibrary));

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

export function generatedRoute<P extends AnyRoute>(parent: P) {
  return createRoute({ getParentRoute: () => parent, path: "/generated", component: GeneratedPage });
}
