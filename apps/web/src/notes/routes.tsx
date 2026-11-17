import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import { NotesPage, type NotesSearch } from "./NotesPage";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COLORS = new Set(["lime", "violet", "amber"]);
const ANCHORS = new Set(["section", "question", "detached"]);

export function parseNotesSearch(s: Record<string, unknown>): NotesSearch {
  return {
    tab: s.tab === "notebooks" ? "notebooks" : undefined,
    note: typeof s.note === "string" && UUID.test(s.note) ? s.note : undefined,
    color: typeof s.color === "string" && COLORS.has(s.color) ? (s.color as NotesSearch["color"]) : undefined,
    anchor: typeof s.anchor === "string" && ANCHORS.has(s.anchor) ? (s.anchor as NotesSearch["anchor"]) : undefined,
    q: typeof s.q === "string" && s.q.trim() ? s.q.slice(0, 200) : undefined,
  };
}

/** `/notes` while the server's `features.notes` flag is on (offline with a saved profile: the device copy). */
function NotesGate({ search }: { search: NotesSearch }) {
  const me = useMe();
  if (!me.data) {
    return me.isError ? (
      <NotesPage search={search} />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (me.data.features.notes === false) return <Navigate to="/" replace />;
  return <NotesPage search={search} />;
}

/** The Notes routes under the protected app layout; `router.tsx` adds `...notesRoutes(appRoute)`. */
export function notesRoutes(parent: AnyRoute) {
  const notes = createRoute({
    getParentRoute: () => parent,
    path: "/notes",
    validateSearch: parseNotesSearch,
    component: function NotesRoute() {
      return <NotesGate search={notes.useSearch()} />;
    },
  });
  return [notes] as const;
}
