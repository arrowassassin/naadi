import { getAuthState, supabaseClient } from "../auth/session";
import { userUploadsBucket } from "../notebooks/whiteboard/files";

/**
 * Note images in the student's own folder of the user-uploads bucket (`{uid}/notes/{note id}/{uuid}.{ext}`; the
 * storage policies of migration 0002 only let a user read and write under their own id). Uploaded and read with
 * supabase-js; the note stores the object key, never a URL. The bucket name comes from the public config
 * (`storage_buckets.user_uploads`); without it the sheet offers no image button.
 */

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"] as const;
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const SIGNED_SECONDS = 60 * 60;

interface Storage {
  bucket: string;
  userId: string;
}

function storage(): Storage | null {
  const s = getAuthState();
  if (s.status !== "signed-in") return null;
  const bucket = userUploadsBucket(s.config);
  return bucket ? { bucket, userId: s.session.user.id } : null;
}

export function imagesAvailable(): boolean {
  return storage() !== null;
}

const EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
};

export type UploadFailure = "type" | "size" | "unavailable" | "failed";

export async function uploadNoteImage(
  noteId: string,
  file: File,
): Promise<{ path: string } | { error: UploadFailure }> {
  const st = storage();
  if (!st) return { error: "unavailable" };
  const ext = EXT[file.type];
  if (!ext) return { error: "type" };
  if (file.size > MAX_IMAGE_BYTES) return { error: "size" };
  const path = `${st.userId}/notes/${noteId}/${crypto.randomUUID()}.${ext}`;
  const client = await supabaseClient().catch(() => null);
  if (!client) return { error: "unavailable" };
  const { error } = await client.storage
    .from(st.bucket)
    .upload(path, file, { contentType: file.type, upsert: false, cacheControl: "3600" });
  return error ? { error: "failed" } : { path };
}

const signed = new Map<string, { url: string; until: number }>();

/** A short-lived URL for one of the student's images (cached until shortly before it expires). */
export async function signedImageUrl(path: string): Promise<string | null> {
  const hit = signed.get(path);
  if (hit && hit.until > Date.now()) return hit.url;
  const st = storage();
  if (!st || !path.startsWith(`${st.userId}/`)) return null;
  const client = await supabaseClient().catch(() => null);
  if (!client) return null;
  const { data, error } = await client.storage.from(st.bucket).createSignedUrl(path, SIGNED_SECONDS);
  if (error || !data?.signedUrl) return null;
  signed.set(path, { url: data.signedUrl, until: Date.now() + (SIGNED_SECONDS - 120) * 1000 });
  return data.signedUrl;
}
