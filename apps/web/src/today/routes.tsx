import { type AnyRoute, createRoute, redirect } from "@tanstack/react-router";
import { safeNext } from "../auth/redirect";
import { getAuthState, startAuth } from "../auth/session";
import { WelcomePage } from "./WelcomePage";

/** `/welcome`: onboarding, a focused page outside the app shell (no navigation until the plan exists). Today
 * sends a student here until `settings.onboarding.completed_at` is set; it stays reachable to change answers. */
export function welcomeRoute(root: AnyRoute) {
  return createRoute({
    getParentRoute: () => root,
    path: "/welcome",
    beforeLoad: async ({ location }) => {
      await startAuth();
      if (getAuthState().status === "signed-out") {
        throw redirect({ to: "/sign-in", search: { redirect: safeNext(location.href) }, replace: true });
      }
    },
    component: WelcomePage,
  });
}
