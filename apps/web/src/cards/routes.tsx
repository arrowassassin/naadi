import { Skeleton } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate } from "@tanstack/react-router";
import { useMe } from "../auth/api";
import { lazyPage } from "../shell/lazyPage";

const ReviewPage = lazyPage(() => import("./ReviewPage").then((m) => m.ReviewPage));

/** `/cards`: only while the server's `features.cards` is on (the flag that also shows the Builder link). */
function CardsGate() {
  const me = useMe();
  if (!me.data) {
    // Offline with no saved profile: review still works from the cards saved on this device.
    return me.isError ? (
      <ReviewPage />
    ) : (
      <div aria-busy="true" className="pt-10">
        <Skeleton className="h-24 w-full rounded-block" />
      </div>
    );
  }
  if (!me.data.features.cards) return <Navigate to="/" replace />;
  return <ReviewPage />;
}

/** One route under the protected app layout; `router.tsx` adds `cardsRoute(appRoute)`. */
export function cardsRoute(parent: AnyRoute) {
  return createRoute({ getParentRoute: () => parent, path: "/cards", component: CardsGate });
}
