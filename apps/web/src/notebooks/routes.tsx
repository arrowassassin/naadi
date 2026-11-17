import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate, Outlet } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMe } from "../auth/api";
import { NotebookPage } from "./NotebookPage";
import { NotebooksShelf } from "./NotebooksShelf";

/** Notebook and page ids are UUIDs; the page is a 1-based number (clamped by the view). */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PAGE = /^[0-9]{1,5}$/;

/** Only while the server's `features.notes` is on; offline with no saved profile, the device copy still opens. */
function NotesGate({ children }: { children: ReactNode }) {
  const me = useMe();
  if (!me.data) {
    return me.isError ? (
      children
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (me.data.features.notes === false) return <Navigate to="/" replace />;
  return children;
}

/**
 * Routes under the protected app layout (`router.tsx` adds `notebooksRoutes(appRoute)`):
 * `/notes/notebooks` (the shelf alone; the Notes page also shows it as a tab), `/notes/notebooks/$id` (→ page 1)
 * and `/notes/notebooks/$id/$page`.
 */
export function notebooksRoutes(parent: AnyRoute) {
  const root = createRoute({ getParentRoute: () => parent, path: "/notes/notebooks", component: Outlet });
  const shelf = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: function ShelfRoute() {
      return (
        <NotesGate>
          <NotebooksShelf headingLevel={1} className="pt-4" />
        </NotesGate>
      );
    },
  });
  const notebook = createRoute({
    getParentRoute: () => root,
    path: "$id",
    params: {
      parse: (p: Record<string, string>) => {
        if (!UUID.test(p.id ?? "")) throw new Error("invalid notebook id");
        return { id: p.id as string };
      },
      stringify: (p: { id: string }) => p,
    },
    component: Outlet,
  });
  const first = createRoute({
    getParentRoute: () => notebook,
    path: "/",
    component: function FirstPageRoute() {
      const { id } = first.useParams();
      return <Navigate to="/notes/notebooks/$id/$page" params={{ id, page: "1" }} replace />;
    },
  });
  const page = createRoute({
    getParentRoute: () => notebook,
    path: "$page",
    params: {
      parse: (p: Record<string, string>) => {
        if (!PAGE.test(p.page ?? "")) throw new Error("invalid page number");
        return { page: p.page as string };
      },
      stringify: (p: { page: string }) => p,
    },
    component: function NotebookPageRoute() {
      const { id, page: n } = page.useParams();
      return (
        <NotesGate>
          <NotebookPage key={id} id={id} page={n} />
        </NotesGate>
      );
    },
  });
  return root.addChildren([shelf, notebook.addChildren([first, page])]);
}
