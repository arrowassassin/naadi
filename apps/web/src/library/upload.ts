import type { LibraryOut, UploadTicket } from "@naadi/shared";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getAuthState } from "../auth/session";
import { reserveUploads, startBatch, uploads } from "./api";

/**
 * Files into the Library (segment 15): pick the content type the upload declares, refuse what the server would
 * refuse before a byte leaves the device, reserve one batch, upload each file straight to Storage with its signed
 * upload token (supabase-js `uploadToSignedUrl`), then start the batch's runs. Uploads need a connection.
 */

export const MB = 1024 * 1024;

/** Content types browsers leave empty (Safari: .md, .heic, .apkg …), by extension. */
const BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: "text/csv",
  txt: "text/plain",
  md: "text/markdown",
  markdown: "text/markdown",
  html: "text/html",
  htm: "text/html",
  epub: "application/epub+zip",
  rtf: "application/rtf",
  zip: "application/zip",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
  avif: "image/avif",
  heic: "image/heic",
  heif: "image/heif",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  aac: "audio/aac",
  wav: "audio/wav",
  ogg: "audio/ogg",
  mp4: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
};
const ANKI_EXTENSIONS = new Set(["apkg", "colpkg"]);
export const ANKI_MIME = "application/vnd.anki";
const ZIP_MAGIC = [0x50, 0x4b, 0x03, 0x04];
const ZIPPISH = new Set(["", "application/zip", "application/x-zip-compressed", "application/octet-stream"]);

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function startsWithZipMagic(head: Uint8Array): boolean {
  return ZIP_MAGIC.every((b, i) => head[i] === b);
}

/**
 * The content type to declare for a file: the browser's own type, except that an Anki package (.apkg/.colpkg that
 * really is a ZIP) is `application/vnd.anki`, and an empty or generic type falls back to the extension (HEIC
 * photos, Markdown). The server sniffs the bytes again and refuses a type that lies.
 */
export function contentTypeFor(name: string, reported: string, head: Uint8Array): string {
  const ext = extensionOf(name);
  const type = reported.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (ANKI_EXTENSIONS.has(ext) && ZIPPISH.has(type) && startsWithZipMagic(head)) return ANKI_MIME;
  if (type && type !== "application/octet-stream") return type;
  return BY_EXTENSION[ext] ?? type;
}

async function head(file: Blob): Promise<Uint8Array> {
  try {
    return new Uint8Array(await file.slice(0, 4).arrayBuffer());
  } catch {
    return new Uint8Array();
  }
}

export type Refusal = "file_too_large" | "unsupported_type" | "empty_file";

export interface Checked {
  file: File;
  contentType: string;
  refusal: Refusal | null;
}

/** Each file with its content type and, when the server would refuse it, why (checked before any upload). */
export async function checkFiles(files: readonly File[], limits: LibraryOut["limits"]): Promise<Checked[]> {
  const allowed = new Set(limits.content_types);
  return Promise.all(
    files.map(async (file) => {
      const contentType = contentTypeFor(file.name, file.type, await head(file));
      let refusal: Refusal | null = null;
      if (file.size <= 0) refusal = "empty_file";
      else if (file.size > limits.max_file_mb * MB) refusal = "file_too_large";
      else if (!allowed.has(contentType)) refusal = "unsupported_type";
      return { file, contentType, refusal };
    }),
  );
}

/** How many files go up at once (an iPad on hotel Wi-Fi should not open 40 uploads). */
export const PARALLEL_UPLOADS = 3;

export interface UploadOutcome {
  batchId: string;
  uploaded: number;
  failed: number;
}

function storageClient(): SupabaseClient | null {
  const s = getAuthState();
  return s.status === "signed-in" ? s.client : null;
}

async function uploadOne(client: SupabaseClient, ticket: UploadTicket, file: File, contentType: string) {
  const { error } = await client.storage
    .from(ticket.bucket)
    .uploadToSignedUrl(ticket.path, ticket.token, file, { contentType, upsert: false });
  return !error;
}

/**
 * Reserve, upload and start accepted files as one batch. Throws (an ApiError with a code such as storage_full or
 * daily_limit) when the reservation is refused; a single file that fails to upload is marked failed on its card
 * and the rest of the batch still starts. `refresh` reloads the Library once the runs have started.
 */
export async function uploadFiles(
  accepted: readonly Checked[],
  refresh: () => Promise<unknown>,
): Promise<UploadOutcome> {
  const client = storageClient();
  if (!client) throw new Error("not signed in");
  const reserved = await reserveUploads(
    accepted.map((c) => ({ name: c.file.name, size: c.file.size, content_type: c.contentType })),
  );
  const jobs = reserved.tickets.map((ticket, i) => ({ ticket, checked: accepted[i] as Checked }));
  uploads.begin(jobs.map((j) => j.ticket.item_id));
  const done: string[] = [];
  let failed = 0;
  let next = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const job = jobs[next++] as (typeof jobs)[number];
      let ok = false;
      try {
        ok = await uploadOne(client, job.ticket, job.checked.file, job.checked.contentType);
      } catch {
        ok = false;
      }
      if (ok) done.push(job.ticket.item_id);
      else {
        failed++;
        uploads.done(job.ticket.item_id, false);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, jobs.length) }, worker));
  // Uploaded files stay "uploading" until their runs have started, so a card never flickers to "not finished".
  try {
    if (done.length) await startBatch(reserved.batch_id);
    await refresh();
  } catch (e) {
    for (const id of done) uploads.done(id, false);
    throw e;
  }
  for (const id of done) uploads.done(id, true);
  return { batchId: reserved.batch_id, uploaded: done.length, failed };
}
