import {
  getInkLayer,
  type InkChangesOut,
  type InkItem,
  type InkLayerOut,
  type InkSurfaceType,
  postInkChanges,
} from "@naadi/shared";
import { authed } from "../../auth/api";
import { ApiError, UNREACHABLE } from "../../auth/apiError";
import { accessToken } from "../../auth/session";
import type { ChangesPayload, InkFailure, InkTransport, LayerPayload } from "./sync";

/** No HTTP answer, or a server that is down: the ink is safe on the device and is sent later. */
export function classifyInkError(err: unknown): InkFailure {
  if (!(err instanceof ApiError)) return "offline";
  if (err.status === null || err.status === 502 || err.status === 503 || err.status === 504) return "offline";
  if (err.status === 409 && err.code === "ink_item_limit") return "limit";
  if (err.status === 429) return "offline"; // rate limited: back off and resend, nothing is wrong with the ink
  return "server";
}

/**
 * The ink API through the generated SDK (validated against the OpenAPI contract), plus a synchronous keepalive
 * POST for `pagehide`. The beacon uses `fetch` directly: the SDK validates and resolves the token asynchronously
 * before it calls fetch, and a page that is being unloaded may not get that far. It reuses the last token the SDK
 * path saw (`lastToken`), so it needs no await; if there is none, the ink simply stays on the device.
 */
/** Known offline: do not even try (no failed request, no console noise); the ink waits on the device. */
function assertOnline(): void {
  if (typeof navigator !== "undefined" && navigator.onLine === false) throw new ApiError(UNREACHABLE, null, null, null);
}

export function apiTransport(surfaceType: InkSurfaceType, surfaceId: string): InkTransport {
  const path = { surface_type: surfaceType, surface_id: surfaceId };
  let lastToken: string | null = null;
  let inflight = new AbortController();
  const remember = () =>
    void accessToken().then(
      (t) => {
        lastToken = t;
      },
      () => undefined,
    );
  remember();
  return {
    async load(since): Promise<LayerPayload> {
      assertOnline();
      const out = await authed<InkLayerOut>((headers) =>
        // keepalive: a load still running when the page navigates away is not torn down by the unload (WebKit
        // reports such a cancelled request as a page error); it has no body, so the 64 KB limit does not apply.
        getInkLayer({ path, query: since === null ? undefined : { since }, headers, keepalive: true }),
      );
      remember();
      return { rev: out.rev, items: out.items as InkItem[], reset: out.reset, outdated: out.outdated };
    },
    async send(baseRev, items, { keepalive }): Promise<ChangesPayload> {
      assertOnline();
      const out = await authed<InkChangesOut>((headers) =>
        postInkChanges({ path, body: { base_rev: baseRev, items }, headers, keepalive, signal: inflight.signal }),
      );
      remember();
      return {
        rev: out.rev,
        applied: out.applied,
        rejected: out.rejected,
        remote: out.remote as InkItem[],
        reset: out.reset,
      };
    },
    // Ordinary (non-keepalive) sends in flight when the page goes away; their items stay pending on the device.
    abort() {
      inflight.abort();
      inflight = new AbortController();
    },
    beacon(baseRev, items) {
      if (!lastToken || (typeof navigator !== "undefined" && navigator.onLine === false)) return false;
      try {
        // keepalive: the request outlives the page (body under 64 KB, which sync.chunk guarantees)
        void fetch(`/api/ink/${encodeURIComponent(surfaceType)}/${encodeURIComponent(surfaceId)}/changes`, {
          method: "POST",
          keepalive: true,
          headers: { authorization: `Bearer ${lastToken}`, "content-type": "application/json" },
          body: JSON.stringify({ base_rev: baseRev, items }),
        }).catch(() => undefined);
        return true;
      } catch {
        return false;
      }
    },
  };
}
