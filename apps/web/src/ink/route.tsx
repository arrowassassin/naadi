import { getPublicConfig, type PublicConfig } from "@naadi/shared";
import { ErrorState, Skeleton } from "@naadi/ui";
import { useQuery } from "@tanstack/react-query";
import { type AnyRoute, createRoute, Link } from "@tanstack/react-router";
import { lazy, type ReactNode, Suspense } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { useInkCopy } from "./copy";

const InkLab = lazy(() => import("./InkLab").then((m) => ({ default: m.InkLab })));

/** Why /api/public-config failed, as structured fields; the gate words it with ui_copy. */
class PublicConfigError extends Error {
  readonly code: "unreachable" | "contract" | "http";
  readonly status: number | null;
  readonly requestId: string | null;
  constructor(code: PublicConfigError["code"], status: number | null, requestId: string | null) {
    super(code);
    this.name = "PublicConfigError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

/** GET /api/public-config (zod-validated by the generated client). The server decides the feature flags. */
export async function fetchPublicConfig(signal?: AbortSignal): Promise<PublicConfig> {
  let res: Awaited<ReturnType<typeof getPublicConfig>>;
  try {
    res = await getPublicConfig({ signal });
  } catch {
    throw new PublicConfigError("unreachable", null, null);
  }
  const requestId = res.response?.headers.get("x-request-id") ?? null;
  if (res.data && res.response?.ok) return res.data;
  if (res.error instanceof Error && res.error.name === "ZodError")
    throw new PublicConfigError("contract", res.response?.status ?? null, requestId);
  throw new PublicConfigError("http", res.response?.status ?? null, requestId);
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
  const t = useInkCopy();
  return (
    <Shell busy>
      <p role="status" className="m-0 font-mono text-label text-text-3 uppercase">
        {t("ink.gate.opening")}
      </p>
      <Skeleton className="h-12 w-60" />
      <Skeleton className="h-14 w-full" />
      <Skeleton className="aspect-[820/1180] w-full" />
    </Shell>
  );
}

function NotFound() {
  const t = useInkCopy();
  return (
    <Shell>
      <h1 className="m-0 font-display text-headline">{t("ink.gate.not_found")}</h1>
      <p className="m-0 text-text-2">{t("ink.gate.not_found_body")}</p>
      <p className="m-0">
        <Link to="/system">{t("ink.gate.to_system")}</Link>
      </p>
    </Shell>
  );
}

function Failed({ error, onRetry, retrying }: { error: Error; onRetry: () => void; retrying: boolean }) {
  const t = useInkCopy();
  const requestId = error instanceof PublicConfigError || error instanceof ApiError ? error.requestId : null;
  // Our own failures are worded here from their code; /api/me errors arrive already worded by the auth client.
  const message =
    error instanceof PublicConfigError
      ? error.code === "http"
        ? t("ink.gate.http", { status: error.status ?? "?" })
        : t(error.code === "contract" ? "ink.gate.contract" : "ink.gate.unreachable")
      : error.message;
  return (
    <Shell>
      <h1 className="m-0 font-display text-headline">{t("ink.gate.title")}</h1>
      <ErrorState
        title={t("ink.gate.failed")}
        message={message}
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
