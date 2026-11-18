import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import type { Tutor } from "../assistant/useTutor";

/** Render `ui` inside a memory router (at `/`) and a fresh query client; other paths render "destination". */
export function renderInApp(ui: () => ReactNode, extraPaths: string[] = []) {
  const root = createRootRoute({ component: Outlet });
  const index = createRoute({ getParentRoute: () => root, path: "/", component: () => <>{ui()}</> });
  const others = extraPaths.map((p) =>
    createRoute({ getParentRoute: () => root, path: p, component: () => <p>destination</p> }),
  );
  const router = createRouter({
    routeTree: root.addChildren([index, ...others]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, qc };
}

/** A tutor with no stream behind it: the state the UI renders, and spies for what it calls. */
export function fakeTutor(over: Partial<Tutor> = {}): Tutor {
  return {
    phase: "idle",
    messages: [],
    isRunning: false,
    threadId: null,
    blocks: [],
    citations: [],
    canvasRefs: [],
    confirm: null,
    tool: null,
    turnNotice: null,
    error: null,
    refreshing: false,
    refreshFailed: false,
    disclaimerShown: false,
    send: vi.fn(async () => {}),
    resume: vi.fn(async () => {}),
    retry: vi.fn(async () => {}),
    continueAfterRefresh: vi.fn(async () => {}),
    openThread: vi.fn(),
    stop: vi.fn(),
    ...over,
  } as Tutor;
}
