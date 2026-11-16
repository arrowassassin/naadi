import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import type { Scope } from "./api";
import { SearchPage, type SearchRouteSearch } from "./SearchPage";

const SCOPES: readonly Scope[] = ["all", "chapters", "notes", "handwriting", "questions", "uploads", "pathway", "web"];

export function parseSearchSearch(s: Record<string, unknown>): SearchRouteSearch {
  const scope = SCOPES.find((x) => x === s.scope);
  return {
    q: typeof s.q === "string" && s.q.trim() ? s.q.slice(0, 300) : undefined,
    scope: scope && scope !== "all" ? scope : undefined,
    web: s.web === 1 || s.web === "1" ? 1 : undefined,
  };
}

/** `/search`: only while the server's `features.search` is on (the same flag that shows the nav button). */
function SearchGate({ search }: { search: SearchRouteSearch }) {
  const me = useMe();
  if (!me.data) {
    return me.isError ? (
      <SearchPage search={search} />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (!me.data.features.search) return <Navigate to="/" replace />;
  return <SearchPage search={search} />;
}

/** One route under the protected app layout; `router.tsx` adds `searchRoute(appRoute)`. */
export function searchRoute(parent: AnyRoute) {
  const route = createRoute({
    getParentRoute: () => parent,
    path: "/search",
    validateSearch: parseSearchSearch,
    component: function SearchRoute() {
      return <SearchGate search={route.useSearch()} />;
    },
  });
  return route;
}
