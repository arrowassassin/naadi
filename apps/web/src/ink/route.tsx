import { type AnyRoute, createRoute, lazyRouteComponent } from "@tanstack/react-router";

/** `/system/ink-lab`: latency harness and demo surface for the ink engine (dev/admin tool). */
export function inkLabRoute<P extends AnyRoute>(parent: P) {
  return createRoute({
    getParentRoute: () => parent,
    path: "/system/ink-lab",
    component: lazyRouteComponent(() => import("./InkLab"), "InkLab"),
  });
}
