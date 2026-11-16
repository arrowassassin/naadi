import { getPublicConfig, type PublicConfig } from "@naadi/shared";
import { ErrorState, Skeleton } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { type AnyRoute, createRoute, Link } from "@tanstack/react-router";
import { lazy, type ReactNode, Suspense } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";

const InkLab = lazy(() => import("./InkLab").then((m) => ({ default: m.InkLab })));

class PublicConfigError extends Error {
  readonly requestId: string | null;
  constructor(message: string, requestId: string | null) {
    super(message);
    this.name = "PublicConfigError";
    this.requestId = requestId;
  }
}

/** GET /api/public-config (zod-validated by the generated client). The server decides the feature flags. */
export async function fetchPublicConfig(signal?: AbortSignal): Promise<PublicConfig> {
  let res: Awaited<ReturnType<typeof getPublicConfig>>;
  try {
    res = await getPublicConfig({ signal });
  } catch {
    throw new PublicConfigError("The API could not be reached. Check your connection and try again.", null);
  }
  const requestId = res.response?.headers.get("x-request-id") ?? null;
  if (res.data && res.response?.ok) return res.data;
  if (res.error instanceof Error && res.error.name === "ZodError")
    throw new PublicConfigError("The API sent a response this version of the app does not understand.", requestId);
  const status = res.response ? ` (HTTP ${res.response.status})` : "";
  throw new PublicConfigError(`The app configuration could not be loaded${status}.`, requestId);
}

function Shell({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <main
      id="main"
      tabIndex={-1}
      aria-busy={busy}
      className="mx-auto flex max-w-(--content-max) flex-col gap-4 px-(--gutter) pt-8 pb-24 tablet:pt-12"
    >
      {children}
    </main>
  );
}

function Loading() {
  return (
    <Shell busy>
      <p role="status" className="m-0 font-mono text-label text-text-3 uppercase">
        Opening the ink lab…
      </p>
      <Skeleton className="h-12 w-60" />
      <Skeleton className="h-14 w-full" />
      <Skeleton className="aspect-[820/1180] w-full" />
    </Shell>
  );
}

function NotFound() {
  return (
    <Shell>
      <h1 className="m-0 font-display text-headline">Page not found</h1>
      <p className="m-0 text-text-2">There is nothing at this address.</p>
      <p className="m-0">
        <Link to="/system">Open the design system</Link>
      </p>
    </Shell>
  );
}

function Failed({ error, onRetry, retrying }: { error: Error; onRetry: () => void; retrying: boolean }) {
  const requestId = error instanceof PublicConfigError || error instanceof ApiError ? error.requestId : null;
  return (
    <Shell>
      <h1 className="m-0 font-display text-headline">Ink lab</h1>
      <ErrorState
        title="The ink lab could not be opened"
        message={error.message}
        requestId={requestId}
        onRetry={onRetry}
        retrying={retrying}
      />
    </Shell>
  );
}

function Lab() {
  return (
    <Suspense fallback={<Loading />}>
      <InkLab />
    </Suspense>
  );
}

/** Signed in: the server decides per user (/api/me `features.ink_lab`: admins, or everyone outside prod). */
function SignedInGate() {
  const me = useMe();
  if (me.isError && !me.data)
    return <Failed error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />;
  if (!me.data) return <Loading />;
  return me.data.features.ink_lab ? <Lab /> : <NotFound />;
}

/** Auth not configured or unreachable: fall back to the public flag. */
function PublicGate() {
  const q = useQuery({
    queryKey: ["public-config"],
    queryFn: ({ signal }) => fetchPublicConfig(signal),
    staleTime: 60_000,
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <Failed error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  return q.data.features.ink_lab ? <Lab /> : <NotFound />;
}

/**
 * The ink lab is a developer/admin tool, and whether it exists is the server's call, never the bundle's:
 * signed in, `features.ink_lab` from /api/me (the app_config flag with its audience, e.g. admins only
 * in production); signed out, the public flag from /api/public-config. The lab's code is only
 * downloaded once the flag says yes.
 */
export function InkLabGate() {
  const auth = useAuth();
  if (auth.status === "loading") return <Loading />;
  if (auth.status === "signed-in") return <SignedInGate />;
  if (auth.status === "signed-out") return auth.config.features.ink_lab ? <Lab /> : <NotFound />;
  return <PublicGate />;
}

/** `/system/ink-lab`: latency harness and demo surface for the ink engine. */
export function inkLabRoute<P extends AnyRoute>(parent: P) {
  return createRoute({ getParentRoute: () => parent, path: "/system/ink-lab", component: InkLabGate });
}
