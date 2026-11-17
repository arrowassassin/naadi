import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  addImageFiles,
  attachmentFromContext,
  imageDataUrl,
  MAX_ATTACHMENTS,
  sniffImageType,
  toAttachmentsIn,
} from "./attachments";
import { allowedUrl, imagePrefixes, SEARCH_MEDIA_HOSTS } from "./mediaHosts";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0]);
const WEBP = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]);
const GIF = new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]);

const file = (bytes: Uint8Array<ArrayBuffer>, name: string, type = "image/png") => new File([bytes], name, { type });

describe("attachments", () => {
  it("knows an image by its bytes, not its name", () => {
    expect(sniffImageType(PNG)).toBe("image/png");
    expect(sniffImageType(JPEG)).toBe("image/jpeg");
    expect(sniffImageType(WEBP)).toBe("image/webp");
    expect(sniffImageType(GIF)).toBeNull();
  });

  it("makes data URLs for png/jpeg/webp up to 1.5 MB, refuses the rest", async () => {
    expect(await imageDataUrl(new Blob([PNG]))).toEqual({ dataUrl: expect.stringMatching(/^data:image\/png;base64,/) });
    expect(await imageDataUrl(new Blob([GIF]))).toEqual({ error: "type" });
    expect(await imageDataUrl(new Blob([new Uint8Array(1.5 * 1024 * 1024 + 1)]))).toEqual({ error: "too_big" });
  });

  it(`keeps at most ${MAX_ATTACHMENTS} and reports the first problem`, async () => {
    const files = [
      file(PNG, "a.png"),
      file(GIF, "b.gif", "image/gif"),
      file(JPEG, "c.jpg"),
      file(WEBP, "d.webp"),
      file(PNG, "e.png"),
      file(PNG, "f.png"),
    ];
    const r = await addImageFiles([], files);
    expect(r.attachments.map((a) => (a.kind === "image" ? a.name : ""))).toEqual(["a.png", "c.jpg", "d.webp", "e.png"]);
    expect(r.error).toBe("type");
    const full = await addImageFiles(r.attachments, [file(PNG, "g.png")]);
    expect(full.attachments).toHaveLength(MAX_ATTACHMENTS);
    expect(full.error).toBe("too_many");
  });

  it("turns a selection and a lasso PNG into attachments with their surface", async () => {
    const sel = await attachmentFromContext({
      type: "selection",
      surface: { type: "section", id: "s1" },
      text: "  anion gap  ",
    });
    const lasso = await attachmentFromContext({
      type: "ink_lasso",
      surface: { type: "section", id: "s1" },
      png: new Blob([PNG]),
      text: "",
    });
    expect(sel && "attachment" in sel ? sel.attachment : null).toMatchObject({ kind: "selection", text: "anion gap" });
    expect(lasso && "attachment" in lasso ? lasso.attachment : null).toMatchObject({
      kind: "image",
      surface: { type: "section", id: "s1" },
      text: null,
    });
    expect(await attachmentFromContext({ type: "flashcard", id: "c1" })).toBeNull();
    // S15's PDF lasso: the circled region becomes an image on the pdf_page surface; no image, no attachment.
    const pdf = await attachmentFromContext({ type: "pdf_page", id: "u1:3", image: new Blob([PNG]) });
    expect(pdf && "attachment" in pdf ? pdf.attachment : null).toMatchObject({
      kind: "image",
      surface: { type: "pdf_page", id: "u1:3" },
    });
    expect(await attachmentFromContext({ type: "pdf_page", id: "u1:3", image: null })).toBeNull();
    const wire = toAttachmentsIn([
      ...(sel && "attachment" in sel ? [sel.attachment] : []),
      ...(lasso && "attachment" in lasso ? [lasso.attachment] : []),
    ]);
    expect(wire[0]).toEqual({ kind: "selection", text: "anion gap", surface: { type: "section", id: "s1" } });
    expect(wire[1]).toMatchObject({ kind: "image", data_url: expect.stringMatching(/^data:image\/png/) });
  });
});

describe("chat media hosts", () => {
  it("match the server CSP's img-src search hosts (no drift)", () => {
    const security = readFileSync(path.resolve(__dirname, "../../../api/naadi_api/security.py"), "utf8");
    const block = security.slice(
      security.indexOf("SEARCH_MEDIA_HOSTS = ("),
      security.indexOf(")", security.indexOf("SEARCH_MEDIA_HOSTS = (")),
    );
    const hosts = [...block.matchAll(/"(https:\/\/[^"]+)"/g)].map((m) => m[1]);
    expect(hosts).toEqual([...SEARCH_MEDIA_HOSTS]);
  });

  it("allow only https from those hosts, the app origin and Supabase storage", () => {
    const origin = "https://naadi.example";
    const prefixes = imagePrefixes(origin, "https://abc.supabase.co");
    expect(allowedUrl("https://upload.wikimedia.org/x.png", prefixes, origin)).toBe(true);
    expect(allowedUrl("https://abc.supabase.co/storage/v1/object/x.png", prefixes, origin)).toBe(true);
    expect(allowedUrl("/media/x.png", prefixes, origin)).toBe(true);
    expect(allowedUrl("https://upload.wikimedia.org.evil.example/x.png", prefixes, origin)).toBe(false);
    expect(allowedUrl("http://upload.wikimedia.org/x.png", prefixes, origin)).toBe(false);
    expect(allowedUrl("https://user:pw@upload.wikimedia.org/x.png", prefixes, origin)).toBe(false);
    expect(allowedUrl("data:image/png;base64,AAAA", prefixes, origin)).toBe(false);
    expect(allowedUrl("https://abc.supabase.co/auth/v1/x", prefixes, origin)).toBe(false);
  });
});
