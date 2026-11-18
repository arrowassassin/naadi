/**
 * The whiteboard scene as stored in `notes.body` (canvas 'whiteboard'): Excalidraw's own element JSON (so a
 * scene round-trips through `restore` losslessly), the few appState fields worth keeping, and image references
 * instead of data: URLs (see files.ts). Pure, no Excalidraw import: the editor chunk stays lazy.
 */

export const SCENE_TYPE = "excalidraw";
export const SCENE_VERSION = 2;

export interface StoredFile {
  id: string;
  mimeType: string;
  /** Object key in the user-uploads bucket. */
  path: string;
  created: number;
}
/** In memory (and in an offline-queued save) a file may still be a data: URL. */
export interface InlineFile {
  id: string;
  mimeType: string;
  dataURL: string;
  created?: number;
}
export type SceneFile = StoredFile | InlineFile;

export function isStoredFile(f: unknown): f is StoredFile {
  return !!f && typeof f === "object" && typeof (f as StoredFile).path === "string";
}

export interface StoredScene {
  type: typeof SCENE_TYPE;
  version: typeof SCENE_VERSION;
  source: "naadi";
  elements: Record<string, unknown>[];
  appState: { viewBackgroundColor?: string; gridSize?: number | null };
  files: Record<string, SceneFile>;
}

export function emptyScene(): StoredScene {
  return { type: SCENE_TYPE, version: SCENE_VERSION, source: "naadi", elements: [], appState: {}, files: {} };
}

/** Read a stored body; anything that is not a scene gives an empty one (never throws). */
export function readScene(body: unknown): StoredScene {
  if (!body || typeof body !== "object") return emptyScene();
  const b = body as Partial<StoredScene>;
  const elements = Array.isArray(b.elements) ? b.elements.filter((e) => !!e && typeof e === "object") : [];
  const files: Record<string, SceneFile> = {};
  if (b.files && typeof b.files === "object")
    for (const [id, f] of Object.entries(b.files)) {
      if (isStoredFile(f) || (f && typeof (f as InlineFile).dataURL === "string")) files[id] = f as SceneFile;
    }
  const app = b.appState && typeof b.appState === "object" ? b.appState : {};
  return {
    type: SCENE_TYPE,
    version: SCENE_VERSION,
    source: "naadi",
    elements: elements as Record<string, unknown>[],
    appState: {
      ...(typeof app.viewBackgroundColor === "string" ? { viewBackgroundColor: app.viewBackgroundColor } : {}),
      ...(typeof app.gridSize === "number" || app.gridSize === null ? { gridSize: app.gridSize } : {}),
    },
    files,
  };
}

interface ElementLike {
  id?: unknown;
  isDeleted?: unknown;
  type?: unknown;
  fileId?: unknown;
  version?: unknown;
}

/**
 * Build the body to save from Excalidraw's onChange output: deleted elements are dropped (no collaboration to
 * reconcile with), only files referenced by a live image element are kept, and files already stored keep
 * their reference (`known`) instead of their data: URL.
 */
export function toStoredScene(
  elements: readonly ElementLike[],
  appState: { viewBackgroundColor?: string; gridSize?: number | null },
  files: Record<string, { id: string; mimeType: string; dataURL: string; created?: number }>,
  known: Readonly<Record<string, SceneFile>>,
): StoredScene {
  const live = elements.filter((e) => !e.isDeleted);
  const used = new Set(
    live.filter((e) => e.type === "image" && typeof e.fileId === "string").map((e) => e.fileId as string),
  );
  const out: Record<string, SceneFile> = {};
  for (const id of used) {
    const k = known[id];
    if (k && isStoredFile(k)) out[id] = k;
    else if (files[id]) {
      const f = files[id];
      out[id] = { id, mimeType: f.mimeType, dataURL: f.dataURL, created: f.created ?? Date.now() };
    } else if (k) out[id] = k;
  }
  return {
    type: SCENE_TYPE,
    version: SCENE_VERSION,
    source: "naadi",
    elements: live.map((e) => ({ ...(e as Record<string, unknown>) })),
    appState: {
      ...(appState.viewBackgroundColor ? { viewBackgroundColor: appState.viewBackgroundColor } : {}),
      ...(appState.gridSize !== undefined ? { gridSize: appState.gridSize } : {}),
    },
    files: out,
  };
}

/**
 * A cheap fingerprint of what would be saved: element ids + versions, file ids and the background. Excalidraw
 * calls onChange on every pointer move and selection; only a real change is saved.
 */
export function sceneFingerprint(scene: Pick<StoredScene, "elements" | "files" | "appState">): string {
  const el = scene.elements
    .map((e) => `${String((e as ElementLike).id)}:${String((e as ElementLike).version)}`)
    .join(",");
  return `${el}|${Object.keys(scene.files).sort().join(",")}|${scene.appState.viewBackgroundColor ?? ""}`;
}

/** Text of the scene's text elements (what the server indexes), for tests and the empty-state check. */
export function sceneText(scene: Pick<StoredScene, "elements">): string {
  return scene.elements
    .filter((e) => e.type === "text" && typeof e.text === "string")
    .map((e) => String(e.text))
    .join("\n");
}

/** Bytes of the JSON body (the API refuses bodies over 1 MB). */
export function sceneBytes(scene: StoredScene): number {
  return new TextEncoder().encode(JSON.stringify(scene)).length;
}
