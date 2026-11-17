import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate, Outlet } from "@tanstack/react-router";
import { lazy, type ReactNode, Suspense } from "react";
import { useMe } from "../auth/api";

const LibraryPage = lazy(() => import("./LibraryPage").then((m) => ({ default: m.LibraryPage })));
const PdfPage = lazy(() => import("./pdf/PdfPage").then((m) => ({ default: m.PdfPage })));

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export interface PdfSearch {
  page?: number;
}

export function parsePdfSearch(s: Record<string, unknown>): PdfSearch {
  const raw = typeof s.page === "number" ? s.page : typeof s.page === "string" ? Number(s.page) : Number.NaN;
  return Number.isInteger(raw) && raw >= 1 && raw <= 10_000 ? { page: raw } : {};
}

/** Only while `features.library` is on (like /generated); otherwise back to the course map. */
function Gate({ children }: { children: (features: Record<string, boolean>) => ReactNode }) {
  const me = useMe();
  if (!me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (!me.data.features.library) return <Navigate to="/learn" replace />;
  return <Suspense fallback={<Skeleton className="mt-10 h-40 w-full" />}>{children(me.data.features)}</Suspense>;
}

/**
 * `/learn/library` and `/learn/library/pdf/$uploadId?page=` under Learn (segment 15: no top-level nav item; the
 * Learn item stays current). `learnRoutes` adds them before `$chapterId`.
 */
export function libraryRoutes(learn: AnyRoute) {
  const library = createRoute({ getParentRoute: () => learn, path: "library", component: Outlet });
  const index = createRoute({
    getParentRoute: () => library,
    path: "/",
    component: function LibraryRoute() {
      return <Gate>{(f) => <LibraryPage generation={Boolean(f.generation)} />}</Gate>;
    },
  });
  const pdf = createRoute({
    getParentRoute: () => library,
    path: "pdf/$uploadId",
    params: {
      parse: (p: Record<string, string>) => {
        const id = (p.uploadId ?? "").toLowerCase();
        if (!UUID.test(id)) throw new Error("invalid upload id");
        return { uploadId: id };
      },
      stringify: (p: { uploadId: string }) => p,
    },
    validateSearch: parsePdfSearch,
    component: function PdfRoute() {
      const { uploadId } = pdf.useParams();
      const { page } = pdf.useSearch();
      return <Gate>{() => <PdfPage uploadId={uploadId} page={page ?? 1} />}</Gate>;
    },
  });
  return library.addChildren([index, pdf]);
}
