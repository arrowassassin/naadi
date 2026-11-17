import "@excalidraw/excalidraw/index.css";
import "./whiteboard.css";
import { Excalidraw, MainMenu } from "@excalidraw/excalidraw";
import type { AppState, BinaryFiles, ExcalidrawImperativeAPI } from "@excalidraw/excalidraw/types";
import { useEffect, useMemo, useRef, useState } from "react";
import { type SceneFile, type StoredScene, sceneBytes, sceneFingerprint, toStoredScene } from "./scene";

export interface WhiteboardEditorProps {
  scene: StoredScene;
  /** Images of the scene as data: URLs (loaded by the caller). */
  files: { id: string; mimeType: string; dataURL: string; created: number }[];
  /** Pasting and inserting images (off when uploads are not available). */
  images: boolean;
  /** The page colour (the --color-paper token, resolved by the caller). */
  background: string;
  /** A real change, debounced by the caller. */
  onChange: (scene: StoredScene, bytes: number) => void;
  label: string;
}

/**
 * Excalidraw 0.18.1 (lazy chunk): a light, paper-coloured board with collaboration, share, export-to-link,
 * open/save-file, the library browser and the project links removed. The scene is reported only when it really
 * changed (element versions, files, background), with its size so the caller can refuse an oversized save.
 */
export default function WhiteboardEditor({ scene, files, images, background, onChange, label }: WhiteboardEditorProps) {
  const api = useRef<ExcalidrawImperativeAPI | null>(null);
  const last = useRef(sceneFingerprint(scene));
  const known = useRef<Record<string, SceneFile>>(scene.files);
  const cb = useRef(onChange);
  cb.current = onChange;
  // Live element count on the section (a diagnostic for tests and assistive tech; it changes only with the count).
  const [count, setCount] = useState(() => scene.elements.length);

  // The editor is keyed by page: its initial data is read once per mount.
  // biome-ignore lint/correctness/useExhaustiveDependencies: initial data only; later changes come from the editor
  const initialData = useMemo(
    () => ({
      elements: scene.elements as never,
      appState: {
        viewBackgroundColor: scene.appState.viewBackgroundColor ?? background,
        gridSize: scene.appState.gridSize ?? undefined,
      },
      files: Object.fromEntries(files.map((f) => [f.id, f])) as unknown as BinaryFiles,
      scrollToContent: true,
    }),
    [],
  );

  // Files that arrive after mount (signed URLs resolved later) are added to the running scene.
  useEffect(() => {
    if (files.length) api.current?.addFiles(files as never);
  }, [files]);

  return (
    <section aria-label={label} className="naadi-whiteboard h-full w-full" data-whiteboard="" data-elements={count}>
      <Excalidraw
        initialData={initialData}
        excalidrawAPI={(a) => {
          api.current = a;
        }}
        onChange={(elements, appState: AppState, bin: BinaryFiles) => {
          const next = toStoredScene(
            elements,
            { viewBackgroundColor: appState.viewBackgroundColor, gridSize: appState.gridSize },
            bin as unknown as Record<string, { id: string; mimeType: string; dataURL: string; created?: number }>,
            known.current,
          );
          const fp = sceneFingerprint(next);
          if (fp === last.current) return;
          last.current = fp;
          setCount(next.elements.length);
          // Size as stored: an uploaded image is a short reference, so measure with references.
          const asStored = {
            ...next,
            files: Object.fromEntries(
              Object.entries(next.files).map(([id, f]) => [
                id,
                { id, mimeType: f.mimeType, path: "x".repeat(96), created: 0 },
              ]),
            ),
          } as StoredScene;
          cb.current(next, sceneBytes(asStored));
        }}
        theme="light"
        isCollaborating={false}
        aiEnabled={false}
        handleKeyboardGlobally={false}
        autoFocus={false}
        validateEmbeddable={() => false}
        renderTopRightUI={() => null}
        UIOptions={{
          canvasActions: {
            changeViewBackgroundColor: true,
            clearCanvas: true,
            export: false,
            loadScene: false,
            saveAsImage: false,
            saveToActiveFile: false,
            toggleTheme: null,
          },
          tools: { image: images },
        }}
      >
        <MainMenu>
          <MainMenu.DefaultItems.ClearCanvas />
          <MainMenu.DefaultItems.ChangeCanvasBackground />
        </MainMenu>
      </Excalidraw>
    </section>
  );
}
