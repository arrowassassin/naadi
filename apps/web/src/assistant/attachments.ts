import type { AssistantAskContext } from "./ask";
import type { AttachmentIn } from "./protocol";
import { MAX_SELECTION_CHARS } from "./protocol";
import { nextAttachmentId, type PendingAttachment } from "./store";

/** Protocol limits: ≤ 4 attachments per message (app_config.assistant.max_attachments), images ≤ 1.5 MB. */
export const MAX_ATTACHMENTS = 4;
export const MAX_IMAGE_BYTES = 1.5 * 1024 * 1024;
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;

export type AttachError = "type" | "too_big" | "too_many" | "unreadable";

/** The file's real type from its first bytes (the browser's `file.type` comes from the name). */
export function sniffImageType(bytes: Uint8Array): (typeof IMAGE_TYPES)[number] | null {
  if (bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) {
    return "image/png";
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return "image/webp";
  }
  return null;
}

async function readBytes(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === "function") return new Uint8Array(await blob.arrayBuffer());
  return new Uint8Array(
    await new Promise<ArrayBuffer>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result as ArrayBuffer);
      r.onerror = () => reject(r.error);
      r.readAsArrayBuffer(blob);
    }),
  );
}

function base64(bytes: Uint8Array): string {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(bin);
}

/** A PNG/JPEG/WebP blob as a data URL, or the reason it cannot be attached. */
export async function imageDataUrl(blob: Blob): Promise<{ dataUrl: string } | { error: AttachError }> {
  if (blob.size > MAX_IMAGE_BYTES) return { error: "too_big" };
  let bytes: Uint8Array;
  try {
    bytes = await readBytes(blob);
  } catch {
    return { error: "unreadable" };
  }
  const type = sniffImageType(bytes);
  if (!type) return { error: "type" };
  return { dataUrl: `data:${type};base64,${base64(bytes)}` };
}

/** Add picked files to the pending list; returns the new list and the first problem, if any. */
export async function addImageFiles(
  current: PendingAttachment[],
  files: readonly File[],
): Promise<{ attachments: PendingAttachment[]; error: AttachError | null }> {
  const out = [...current];
  let error: AttachError | null = null;
  for (const f of files) {
    if (out.length >= MAX_ATTACHMENTS) {
      error ??= "too_many";
      break;
    }
    const r = await imageDataUrl(f);
    if ("error" in r) {
      error ??= r.error;
      continue;
    }
    out.push({ id: nextAttachmentId(), kind: "image", dataUrl: r.dataUrl, name: f.name, text: null, surface: null });
  }
  return { attachments: out, error };
}

function clip(text: string, max: number): string {
  return Array.from(text).slice(0, max).join("");
}

/** The pending attachment an ask's context brings (selection text, lasso PNG), or null. */
export async function attachmentFromContext(
  ctx: AssistantAskContext,
): Promise<{ attachment: PendingAttachment } | { error: AttachError } | null> {
  if (ctx.type === "selection") {
    const text = clip(ctx.text.trim(), MAX_SELECTION_CHARS);
    if (!text) return null;
    return { attachment: { id: nextAttachmentId(), kind: "selection", text, surface: ctx.surface } };
  }
  if (ctx.type === "ink_lasso") {
    const r = await imageDataUrl(ctx.png);
    if ("error" in r) return { error: r.error };
    return {
      attachment: {
        id: nextAttachmentId(),
        kind: "image",
        dataUrl: r.dataUrl,
        name: "",
        text: ctx.text.trim() ? clip(ctx.text.trim(), MAX_SELECTION_CHARS) : null,
        surface: ctx.surface,
      },
    };
  }
  if (ctx.type === "pdf_page" && ctx.image) {
    const r = await imageDataUrl(ctx.image);
    if ("error" in r) return { error: r.error };
    return {
      attachment: {
        id: nextAttachmentId(),
        kind: "image",
        dataUrl: r.dataUrl,
        name: "",
        text: null,
        surface: { type: "pdf_page", id: ctx.id },
      },
    };
  }
  return null;
}

/** The wire form of the pending attachments (≤ MAX_ATTACHMENTS). */
export function toAttachmentsIn(list: readonly PendingAttachment[]): AttachmentIn[] {
  return list
    .slice(0, MAX_ATTACHMENTS)
    .map((a) =>
      a.kind === "selection"
        ? { kind: "selection", text: a.text, surface: a.surface }
        : { kind: "image", data_url: a.dataUrl, text: a.text, surface: a.surface },
    );
}
