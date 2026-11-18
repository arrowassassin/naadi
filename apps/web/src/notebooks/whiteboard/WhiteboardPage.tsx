import type { NoteOut } from "@naadi/shared";
import { ErrorState, Skeleton } from "@naadi/ui";
import { Component, lazy, type ReactNode, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../../auth/session";
import { useUiText } from "../../copy/useUiText";
import { type NotebookSync, usePageNote } from "../api";
import { ensureExcalidrawAssetPath } from "./assetPath";
import { loadSceneFiles, userUploadsBucket } from "./files";
import { WHITEBOARD_MAX_BYTES } from "./limits";
import { readScene, type StoredScene } from "./scene";

/** The Excalidraw chunk, loaded only when a whiteboard page is opened. */
const WhiteboardEditor = lazy(() => {
  ensureExcalidrawAssetPath();
  return import("./WhiteboardEditor");
});

/** A failed chunk load (offline before the editor was ever cached) shows the error state with a retry. */
class ChunkBoundary extends Component<
  { fallback: (retry: () => void) => ReactNode; children: ReactNode },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? this.props.fallback(() => this.setState({ failed: false })) : this.props.children;
  }
}

/** Saves wait this long after the last change (a stroke in Excalidraw is many onChange calls). */
export const WHITEBOARD_SAVE_MS = 1200;

function usePaperColour(): string {
  return useMemo(() => {
    if (typeof document === "undefined") return "";
    return getComputedStyle(document.documentElement).getPropertyValue("--color-paper").trim();
  }, []);
}

/**
 * A whiteboard page: the stored scene and its images are loaded, then the lazy Excalidraw editor; every real
 * change is saved (debounced) through the notebook queue, so it is kept on the device first and synced after.
 */
export function WhiteboardPage({
  page,
  notebookId,
  sync,
  label,
}: {
  page: NoteOut;
  notebookId: string;
  sync: NotebookSync;
  label: string;
}) {
  const t = useUiText();
  const [toolsOpen, setToolsOpen] = useState(true);
  const auth = useAuth();
  const images = auth.status === "signed-in" && userUploadsBucket(auth.config) !== null;
  const { note, query } = usePageNote(page);
  const background = usePaperColour();
  const [files, setFiles] = useState<{ id: string; mimeType: string; dataURL: string; created: number }[] | null>(null);
  const [tooLarge, setTooLarge] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingScene = useRef<StoredScene | null>(null);
  // The scene is read once per page: later saves come from the editor itself and must not remount it.
  const first = useRef<{ id: string; scene: StoredScene } | null>(null);
  if (note && first.current?.id !== note.id) first.current = { id: note.id, scene: readScene(note.body) };
  const scene = note ? (first.current?.scene ?? null) : null;

  // Load the scene's images once per page.
  useEffect(() => {
    if (!scene) return;
    let alive = true;
    setFiles(null);
    loadSceneFiles(scene.files).then(
      (f) => alive && setFiles(f),
      () => alive && setFiles([]),
    );
    return () => {
      alive = false;
    };
  }, [scene]);

  // Send a pending save when leaving the page (another page, another route, the tab hidden).
  useEffect(() => {
    const flush = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      const s = pendingScene.current;
      pendingScene.current = null;
      if (s)
        void sync.add({
          kind: "patchNote",
          id: page.id,
          notebookId,
          patch: { body: s as unknown as Record<string, unknown> },
        });
    };
    const onHide = () => {
      if (document.visibilityState === "hidden") flush();
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [page.id, notebookId, sync]);

  if (note === undefined || (scene && files === null)) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2" data-whiteboard-loading="">
        <span className="sr-only">{t("whiteboard.loading")}</span>
        <Skeleton className="h-[min(70dvh,720px)] w-full rounded-[18px]" />
      </div>
    );
  }
  if (note === null || !scene) {
    return (
      <ErrorState
        title={t("whiteboard.error_title")}
        message={t("whiteboard.error_body")}
        onRetry={() => void query.refetch()}
        retryLabel={t("notebooks.retry")}
        retrying={query.isFetching}
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {tooLarge ? (
        <p role="alert" className="m-0 rounded-block border border-amber/60 px-4 py-3 text-amber text-ui-sm">
          {t("notebooks.error.note_body_too_large")}
        </p>
      ) : null}
      <button
        type="button"
        aria-expanded={toolsOpen}
        onClick={() => setToolsOpen(!toolsOpen)}
        className="min-h-11 self-start rounded-pill border border-line-control px-3 text-ui-sm"
      >
        {t("ink.toolbar")} <span aria-hidden="true">{toolsOpen ? "▴" : "▾"}</span>
      </button>
      <div
        data-board-tools-hidden={!toolsOpen || undefined}
        className="h-[min(72dvh,760px)] min-h-[420px] overflow-hidden rounded-[18px] border border-line-strong bg-paper"
      >
        <ChunkBoundary
          fallback={(retry) => (
            <div className="p-4">
              <ErrorState
                title={t("whiteboard.error_title")}
                message={t("whiteboard.chunk_failed")}
                onRetry={retry}
                retryLabel={t("notebooks.retry")}
              />
            </div>
          )}
        >
          <Suspense
            fallback={
              <div aria-busy="true" className="flex h-full items-center justify-center text-paper-text-2">
                {t("whiteboard.loading")}
              </div>
            }
          >
            <WhiteboardEditor
              key={page.id}
              scene={scene}
              files={files ?? []}
              images={images}
              background={background}
              label={label}
              onChange={(next, bytes) => {
                if (bytes > WHITEBOARD_MAX_BYTES) {
                  setTooLarge(true);
                  return;
                }
                setTooLarge(false);
                pendingScene.current = next;
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => {
                  timer.current = null;
                  const s = pendingScene.current;
                  pendingScene.current = null;
                  if (s)
                    void sync.add({
                      kind: "patchNote",
                      id: page.id,
                      notebookId,
                      patch: { body: s as unknown as Record<string, unknown> },
                    });
                }, WHITEBOARD_SAVE_MS);
              }}
            />
          </Suspense>
        </ChunkBoundary>
      </div>
    </div>
  );
}
