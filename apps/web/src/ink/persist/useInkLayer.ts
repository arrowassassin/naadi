import type { InkEngine } from "@naadi/ink";
import type { InkDiff, InkItem, InkSurfaceType } from "@naadi/shared";
import { useQuery } from "@tanstack/react-query";
import { type Ref, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../../auth/session";
import { useOnlineStatus } from "../../health/useOnlineStatus";
import { dexieStore, type PendingStore } from "./db";
import { type InkFailure, type InkSaveState, InkSync, type InkTransport } from "./sync";
import { apiTransport, classifyInkError } from "./transport";

/** Other devices' ink is fetched every 30 s while a surface is mounted and the page is visible. */
export const INK_REFETCH_MS = 30_000;

export interface UseInkLayerOptions {
  /** Your own listener; persistence sees every diff either way. */
  onStrokesChange?: (diff: InkDiff) => void;
  /** Your own engine handle (undo/redo buttons, selection actions). */
  engineRef?: Ref<InkEngine | null>;
  /** The page already knows the server is unreachable (e.g. it shows its device copy): do not fetch. */
  offline?: boolean;
  /** Test seams. */
  transport?: InkTransport;
  store?: PendingStore;
}

export interface InkLayerHandle {
  /** The device copy has been read (the canvas may show ink); the network may still be loading. */
  ready: boolean;
  state: InkSaveState;
  /** Loaded from the server and nothing in flight or pending. */
  idle: boolean;
  failure: InkFailure | null;
  /** Live items drawn on an earlier revision of the surface content (section text changed since). */
  outdated: number;
  retry: () => void;
  /** Spread into <InkCanvas> (key the canvas by surface id). */
  canvasProps: {
    items: readonly InkItem[] | undefined;
    onStrokesChange: (diff: InkDiff) => void;
    engineRef: (engine: InkEngine | null) => void;
  };
}

function assignRef<T>(ref: Ref<T> | undefined, value: T): void {
  if (typeof ref === "function") ref(value);
  else if (ref) (ref as { current: T }).current = value;
}

/**
 * Persist and sync one surface's ink (segment 12b): the layer is read from the device at once, loaded and kept
 * fresh from the server (TanStack Query: on mount, focus, visible, reconnect and every 30 s), and every engine diff
 * is recorded, saved to IndexedDB and flushed after 800 ms of quiet or 50 items, on `visibilitychange` hidden
 * (keepalive) and `pagehide` (a synchronous keepalive POST). Remote changes go through InkEngine.applyRemote, so
 * the student's undo history is untouched.
 */
export function useInkLayer(
  surfaceType: InkSurfaceType,
  surfaceId: string,
  options: UseInkLayerOptions = {},
): InkLayerHandle {
  const auth = useAuth();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const opts = useRef(options);
  opts.current = options;
  const engine = useRef<InkEngine | null>(null);
  const queued = useRef<InkItem[]>([]);
  // The live object (edits are recorded even before the device copy is read); `sync` turns the network on.
  const live = useRef<InkSync | null>(null);
  const [sync, setSync] = useState<InkSync | null>(null);
  const [items, setItems] = useState<readonly InkItem[] | undefined>(undefined);
  const [, setTick] = useState(0);
  const online = useOnlineStatus();

  useEffect(() => {
    if (!userId) return;
    const key = `${surfaceType}:${surfaceId}`;
    const s = new InkSync({
      transport: opts.current.transport ?? apiTransport(surfaceType, surfaceId),
      store: opts.current.store ?? dexieStore(userId, key),
      classify: classifyInkError,
      onRemote: (remote) => {
        if (engine.current) engine.current.applyRemote(remote);
        else queued.current.push(...remote);
      },
      onChange: () => setTick((t) => t + 1),
    });
    let alive = true;
    live.current = s;
    setItems(undefined);
    queued.current = [];
    void s.restore().then((restored) => {
      if (!alive) return;
      setItems(restored);
      setSync(s);
    });

    const onHidden = () => {
      if (document.visibilityState === "hidden") void s.flush({ keepalive: true });
    };
    const onPageHide = () => s.beacon();
    const onOnline = () => void s.retry();
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("online", onOnline);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("online", onOnline);
      // Leaving the surface (next section, next card): send what is pending, then stop the timers.
      void s.flush().finally(() => s.stop());
      if (live.current === s) live.current = null;
      setSync(null);
    };
  }, [userId, surfaceType, surfaceId]);

  const query = useQuery({
    queryKey: ["ink", userId, surfaceType, surfaceId],
    // Offline, nothing is asked of the server (the device copy is shown); coming back online refetches.
    enabled: sync !== null && online && !options.offline,
    queryFn: async () => {
      if (!sync) return null;
      await sync.refresh();
      return { rev: sync.cursor, outdated: sync.outdated };
    },
    refetchInterval: INK_REFETCH_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
    staleTime: 0,
    gcTime: 0,
    retry: false,
    // The sync object holds the data; the query only drives when to talk to the server.
    structuralSharing: false,
  });

  const onStrokesChange = useCallback((diff: InkDiff) => {
    live.current?.record(diff);
    opts.current.onStrokesChange?.(diff);
  }, []);

  const engineRef = useCallback((e: InkEngine | null) => {
    engine.current = e;
    if (e && queued.current.length) {
      e.applyRemote(queued.current);
      queued.current = [];
    }
    assignRef(opts.current.engineRef, e);
  }, []);

  const retry = useCallback(() => {
    void sync?.retry();
    void query.refetch();
  }, [sync, query]);

  const canvasProps = useMemo(() => ({ items, onStrokesChange, engineRef }), [items, onStrokesChange, engineRef]);
  return {
    ready: items !== undefined,
    state: sync?.state ?? "loading",
    idle: (sync?.idle ?? false) && !query.isFetching,
    failure: sync?.lastFailure ?? null,
    outdated: query.data?.outdated ?? 0,
    retry,
    canvasProps,
  };
}
