/**
 * Whiteboard images (segment 13): Excalidraw keeps pasted images as data: URLs in memory; the stored scene keeps
 * only a reference to an object in the student's own folder of the user-uploads bucket
 * (`{uid}/notes/<note id>/<file id>.<ext>`, owner-only by the storage policies), so `notes.body` stays small and
 * no image is stored twice. Uploads happen when the queued save is sent (an image added offline waits in the
 * device queue as a data: URL); reads use short-lived signed URLs and are cached for the page's lifetime.
 */
import type { PublicConfig } from "@naadi/shared";
import { getAuthState } from "../../auth/session";
import { isStoredFile, type SceneFile, type StoredFile } from "./scene";

/** Image types the bucket accepts and Excalidraw can show (SVG is refused: user SVGs could carry script). */
export const WHITEBOARD_IMAGE_TYPES: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
};

/** The user-uploads bucket name from the public config (app_config.storage.user_uploads); null when not served. */
export function userUploadsBucket(config: PublicConfig | null | undefined): string | null {
  const b = (config as (PublicConfig & { storage_buckets?: { user_uploads?: unknown } }) | null | undefined)
    ?.storage_buckets?.user_uploads;
  return typeof b === "string" && /^[a-z0-9-]{3,63}$/.test(b) ? b : null;
}

function signedIn() {
  const auth = getAuthState();
  if (auth.status !== "signed-in") throw new Error("whiteboard files: not signed in");
  const bucket = userUploadsBucket(auth.config);
  if (!bucket) throw new Error("whiteboard files: no user-uploads bucket configured");
  return { client: auth.client, userId: auth.session.user.id, bucket };
}

export function filePath(userId: string, noteId: string, fileId: string, mimeType: string): string {
  const ext = WHITEBOARD_IMAGE_TYPES[mimeType] ?? "bin";
  const safe = fileId.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 80) || "file";
  return `${userId}/notes/${noteId}/${safe}.${ext}`;
}

export function dataUrlToBlob(dataUrl: string): Blob {
  const m = /^data:([^;,]+)(;base64)?,(.*)$/s.exec(dataUrl);
  if (!m) throw new Error("whiteboard files: not a data URL");
  const mime = m[1] ?? "application/octet-stream";
  const payload = m[3] ?? "";
  if (!m[2]) return new Blob([decodeURIComponent(payload)], { type: mime });
  const bin = atob(payload);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error ?? new Error("whiteboard files: read failed"));
    r.readAsDataURL(blob);
  });
}

/**
 * Replace every not-yet-uploaded image (a data: URL) in a stored scene by its user-uploads reference, uploading
 * it first. Other bodies pass through unchanged. Throws when an upload fails, so the queued save is retried.
 */
export async function prepareSceneForUpload(
  _userId: string,
  noteId: string,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const files = body.files as Record<string, SceneFile> | undefined;
  if (!files || typeof files !== "object") return body;
  const pending = Object.entries(files).filter(([, f]) => !isStoredFile(f) && typeof f?.dataURL === "string");
  if (!pending.length) return body;
  const { client, userId, bucket } = signedIn();
  const out: Record<string, SceneFile> = { ...files };
  for (const [id, f] of pending) {
    if (!f || isStoredFile(f) || !f.dataURL) continue;
    const done = uploaded.get(`${noteId}/${id}`);
    if (done) {
      out[id] = done;
      continue;
    }
    if (!WHITEBOARD_IMAGE_TYPES[f.mimeType]) {
      delete out[id]; // a type the bucket refuses (never offered by the picker; pasted SVG) is not kept
      continue;
    }
    const path = filePath(userId, noteId, id, f.mimeType);
    const { error } = await client.storage
      .from(bucket)
      .upload(path, dataUrlToBlob(f.dataURL), { upsert: true, contentType: f.mimeType, cacheControl: "31536000" });
    if (error) throw error;
    remember(path, f.dataURL);
    const ref: StoredFile = { id, mimeType: f.mimeType, path, created: f.created ?? Date.now() };
    uploaded.set(`${noteId}/${id}`, ref);
    out[id] = ref;
  }
  return { ...body, files: out };
}

/** Images already uploaded this session (an editor keeps sending its data: URL until the page reloads). */
const uploaded = new Map<string, StoredFile>();
const dataUrls = new Map<string, string>();
function remember(path: string, dataUrl: string): void {
  if (dataUrls.size > 200) dataUrls.clear();
  dataUrls.set(path, dataUrl);
}

/** The images of a scene as data: URLs (Excalidraw's BinaryFileData), signed and fetched in one batch. */
export async function loadSceneFiles(
  files: Record<string, SceneFile> | undefined,
): Promise<{ id: string; mimeType: string; dataURL: string; created: number }[]> {
  if (!files) return [];
  const entries = Object.entries(files);
  const out: { id: string; mimeType: string; dataURL: string; created: number }[] = [];
  const missing: [string, StoredFile][] = [];
  for (const [id, f] of entries) {
    if (!f) continue;
    if (!isStoredFile(f)) {
      if (f.dataURL) out.push({ id, mimeType: f.mimeType, dataURL: f.dataURL, created: f.created ?? 0 });
      continue;
    }
    const cached = dataUrls.get(f.path);
    if (cached) out.push({ id, mimeType: f.mimeType, dataURL: cached, created: f.created });
    else missing.push([id, f]);
  }
  if (!missing.length) return out;
  const { client, bucket } = signedIn();
  const { data, error } = await client.storage.from(bucket).createSignedUrls(
    missing.map(([, f]) => f.path),
    600,
  );
  if (error) throw error;
  const urls = new Map((data ?? []).map((d) => [d.path, d.signedUrl]));
  await Promise.all(
    missing.map(async ([id, f]) => {
      const url = urls.get(f.path);
      if (!url) return; // deleted from storage: the element shows Excalidraw's missing-image box
      const res = await fetch(url);
      if (!res.ok) return;
      const dataURL = await blobToDataUrl(await res.blob());
      remember(f.path, dataURL);
      out.push({ id, mimeType: f.mimeType, dataURL, created: f.created });
    }),
  );
  return out;
}
