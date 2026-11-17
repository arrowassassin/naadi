import { Skeleton } from "@naadi/ui";
import { type ComponentProps, type ComponentType, createElement, lazy, type ReactElement, Suspense } from "react";

/** Loads a page module and returns its component. */
type PageLoader<C> = () => Promise<C>;

/**
 * A route's page as its own chunk (segment 22 performance budget: initial JS <= 250 KB gzip). The page module
 * loads when the route first renders; until then a skeleton holds its place. Use it for every page a route
 * module renders, so editors, the ink engine, the exam and their libraries never ship in the first load:
 *
 *   const ReaderPage = lazyPage(() => import("./ReaderPage").then((m) => m.ReaderPage));
 *
 * Call it at module scope only (as above), never inside a component: each page then keeps one stable component.
 * Types stay importable as `import type { X } from "./Page"` (erased at build time, so no chunk is pulled in).
 */
// biome-ignore lint/suspicious/noExplicitAny: the wrapped page's own props are preserved through C
export function lazyPage<C extends ComponentType<any>>(
  load: PageLoader<C>,
): (props: ComponentProps<C>) => ReactElement {
  const Page = lazy(() => load().then((c) => ({ default: c })));
  const fallback = createElement(Skeleton, { className: "mt-10 h-40 w-full" });
  return (props: ComponentProps<C>) =>
    createElement(Suspense, { fallback }, createElement(Page as ComponentType<ComponentProps<C>>, props));
}
