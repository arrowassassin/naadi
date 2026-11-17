import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, useNavigate } from "@tanstack/react-router";
import { Suspense } from "react";
import { useMe } from "../auth/api";
import { lazyWithFallback } from "../shell/lazyPage";
import { FILTERS, type QueueFilter } from "./api";
import { useReviewCopy } from "./copy";

const ReviewHub = lazyWithFallback(() => import("./ReviewHub").then((m) => m.ReviewHub));
const ReviewItemPage = lazyWithFallback(() => import("./ReviewItemPage").then((m) => m.ReviewItemPage));

const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export interface ReviewSearch {
  filter?: QueueFilter;
}

export function parseReviewSearch(s: Record<string, unknown>): ReviewSearch {
  const f = FILTERS.find((x) => x === s.filter);
  return f && f !== "ai_drafts" ? { filter: f } : {};
}

/** Admins only, and only while the `authoring` flag is on. Anyone else gets the page-not-found treatment. */
function Gate({ children }: { children: React.ReactNode }) {
  const t = useReviewCopy();
  const me = useMe();
  if (!me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (!me.data.is_admin || !me.data.features.authoring) {
    return (
      <section className="flex flex-col gap-3 pt-10" data-testid="review-forbidden">
        <h1 className="m-0 font-display text-headline">{t("review.not_found_title")}</h1>
        <p className="m-0 text-text-2">{t("review.not_found_body")}</p>
      </section>
    );
  }
  return <Suspense fallback={<Skeleton className="mt-10 h-40 w-full" />}>{children}</Suspense>;
}

export function adminReviewRoutes<P extends AnyRoute>(parent: P) {
  const hub = createRoute({
    getParentRoute: () => parent,
    path: "/admin/review",
    validateSearch: parseReviewSearch,
    component: function ReviewHubRoute() {
      const search = hub.useSearch();
      const nav = useNavigate();
      return (
        <Gate>
          <ReviewHub
            filter={search.filter ?? "ai_drafts"}
            onFilter={(f) =>
              void nav({ to: "/admin/review", search: f === "ai_drafts" ? {} : { filter: f }, replace: true })
            }
          />
        </Gate>
      );
    },
  });
  const item = createRoute({
    getParentRoute: () => parent,
    path: "/admin/review/$itemId",
    params: {
      parse: (p: Record<string, string>) => {
        if (!ITEM_ID.test(p.itemId ?? "")) throw new Error("invalid item id");
        return { itemId: p.itemId as string };
      },
    },
    component: function ReviewItemRoute() {
      const { itemId } = item.useParams();
      return (
        <Gate>
          <ReviewItemPage itemId={itemId} />
        </Gate>
      );
    },
  });
  return [hub, item];
}
