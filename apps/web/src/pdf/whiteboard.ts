/**
 * A whiteboard page as a PNG for the PDF (lazy: loads the Excalidraw chunk only when a notebook with whiteboards
 * is exported). The scene comes from the page (or GET /api/notes/{id} when the list omitted it, else this
 * device's copy), its images from user-uploads, and Excalidraw's own exportToBlob draws it on the paper colour.
 */
import { getNote, type NoteOut } from "@naadi/shared";
import { authed } from "../auth/api";
import { getAuthState } from "../auth/session";
import { dexieOutbox } from "../notebooks/outbox";
import { overlayNote } from "../notebooks/overlay";
import { ensureExcalidrawAssetPath } from "../notebooks/whiteboard/assetPath";
import { loadSceneFiles } from "../notebooks/whiteboard/files";
import { readScene } from "../notebooks/whiteboard/scene";
import { cssTokenReader } from "./colors";

/** Longest side of the exported picture, px (sharp on a Letter page at ~240 dpi, small enough for memory). */
const MAX_SIDE = 2400;

async function sceneBody(page: NoteOut): Promise<unknown> {
  const auth = getAuthState();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const store = userId ? dexieOutbox(userId) : null;
  const queue = (await store?.read().catch(() => [])) ?? [];
  if (!page.body_omitted) return overlayNote(page.id, page, queue)?.body ?? null;
  let note: NoteOut | null | undefined;
  try {
    note = await authed<NoteOut>((headers) => getNote({ headers, path: { note_id: page.id } }));
  } catch {
    note = await store?.readSnapshot<NoteOut>(`note:${page.id}`).catch(() => undefined);
  }
  return overlayNote(page.id, note ?? null, queue)?.body ?? null;
}

export async function renderWhiteboardPng(page: NoteOut): Promise<Blob | null> {
  const scene = readScene(await sceneBody(page));
  if (!scene.elements.length) return null;
  ensureExcalidrawAssetPath();
  const [{ exportToBlob }, files] = await Promise.all([
    import("@excalidraw/excalidraw"),
    loadSceneFiles(scene.files).catch(() => []),
  ]);
  const paper = cssTokenReader()("--color-paper").trim();
  return exportToBlob({
    elements: scene.elements as never,
    appState: {
      viewBackgroundColor: scene.appState.viewBackgroundColor ?? paper,
      exportBackground: true,
      exportWithDarkMode: false,
    } as never,
    files: Object.fromEntries(files.map((f) => [f.id, f])) as never,
    mimeType: "image/png",
    exportPadding: 24,
    getDimensions: (width: number, height: number) => {
      const scale = Math.min(2, MAX_SIDE / Math.max(width, height, 1));
      return { width: Math.round(width * scale), height: Math.round(height * scale), scale };
    },
  });
}
