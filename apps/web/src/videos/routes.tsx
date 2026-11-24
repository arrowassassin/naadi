import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import { lazyPage } from "../shell/lazyPage";
import { parseVideosSearch, type VideosSearch } from "./search";

const VideosPage = lazyPage(() => import("./VideosPage").then((m) => m.VideosPage));

/** `/videos`: only while the server's `features.videos` is on (the flag that also shows Videos in the navigation). */
function VideosGate({ search }: { search: VideosSearch }) {
  const me = useMe();
  if (!me.data) {
    return me.isError ? (
      <VideosPage search={search} />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (!me.data.features.videos) return <Navigate to="/" replace />;
  return <VideosPage search={search} />;
}

/** One route under the protected app layout; `router.tsx` adds `videosRoute(appRoute)`. */
export function videosRoute(parent: AnyRoute) {
  const route = createRoute({
    getParentRoute: () => parent,
    path: "/videos",
    validateSearch: parseVideosSearch,
    component: function VideosRoute() {
      return <VideosGate search={route.useSearch()} />;
    },
  });
  return route;
}
