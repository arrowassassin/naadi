import { Skeleton } from "@naadi/ui";
import {
  type ComponentProps,
  type ComponentType,
  createElement,
  lazy,
  type ReactElement,
  type ReactNode,
  Suspense,
  useSyncExternalStore,
} from "react";
import { ChunkUnavailable } from "./ChunkUnavailable";

/** Loads a module and returns its component. */
type Loader<C> = () => Promise<C>;

/**
 * True for the ways browsers and Vite report a code chunk that could not be fetched (offline, a deploy replaced
 * it, the network dropped): Chromium "Failed to fetch dynamically imported module", WebKit "Importing a module
 * script failed", Firefox "error loading dynamically imported module", Vite's "Unable to preload CSS", webpack-style
 * ChunkLoadError. Anything else is a real bug and is not swallowed.
 */
export function isChunkLoadError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  if (err.name === "ChunkLoadError") return true;
  return /dynamically imported module|Importing a module script failed|Unable to preload CSS/i.test(err.message);
}

export interface LazyOptions {
  /** Shown while the chunk loads (default: the page skeleton). */
  fallback?: ReactNode;
  /** How a chunk that cannot load is shown: a page-sized notice (default) or a one-line inline one. */
  unavailable?: "page" | "inline";
}

const PAGE_SKELETON = createElement(Skeleton, { className: "mt-10 h-40 w-full" });

/**
 * A lazily loaded component that never crashes the app when its chunk cannot be fetched (segment 22, lead
 * decision 20261117T124555): the failed import resolves to a "needs a connection" notice (ui_copy) instead of
 * throwing, so no error boundary fires and nothing is logged to the console. The notice retries when the device
 * comes back online, and on its Try again button; a successful retry renders the real component in place.
 * Errors that are not chunk-load failures still reject, so real bugs reach the route's error boundary.
 *
 * Call it at module scope only, never inside a component, so each lazy component stays one stable component:
 *
 *   const ReaderPage = lazyWithFallback(() => import("./ReaderPage").then((m) => m.ReaderPage));
 */
// biome-ignore lint/suspicious/noExplicitAny: the wrapped component's own props are preserved through C
export function lazyWithFallback<C extends ComponentType<any>>(
  load: Loader<C>,
  { fallback = PAGE_SKELETON, unavailable = "page" }: LazyOptions = {},
): (props: ComponentProps<C>) => ReactElement {
  let version = 0;
  const listeners = new Set<() => void>();
  const subscribe = (l: () => void) => {
    listeners.add(l);
    return () => listeners.delete(l);
  };
  const retry = () => {
    Component = make();
    version += 1;
    for (const l of listeners) l();
  };
  const notice = () => createElement(ChunkUnavailable, { variant: unavailable, onRetry: retry });
  const make = () =>
    lazy(() =>
      load().then(
        (c) => ({ default: c as ComponentType<ComponentProps<C>> }),
        (err: unknown) => {
          if (!isChunkLoadError(err)) throw err;
          return { default: notice as ComponentType<ComponentProps<C>> };
        },
      ),
    );
  let Component = make();
  return (props: ComponentProps<C>) => {
    useSyncExternalStore(
      subscribe,
      () => version,
      () => version,
    );
    return createElement(Suspense, { fallback }, createElement(Component, props));
  };
}

/**
 * A route's page as its own chunk (segment 22 performance budget: initial JS <= 250 KB gzip), with the page
 * skeleton while it loads and the "needs a connection" notice if it cannot (see lazyWithFallback). Use it for
 * every page a route module renders, so editors, the ink engine, the exam and their libraries never ship in the
 * first load:
 *
 *   const ReaderPage = lazyPage(() => import("./ReaderPage").then((m) => m.ReaderPage));
 *
 * Types stay importable as `import type { X } from "./Page"` (erased at build time, so no chunk is pulled in).
 */
// biome-ignore lint/suspicious/noExplicitAny: the wrapped page's own props are preserved through C
export function lazyPage<C extends ComponentType<any>>(load: Loader<C>): (props: ComponentProps<C>) => ReactElement {
  return lazyWithFallback(load);
}
