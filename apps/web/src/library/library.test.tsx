import type { IngestItemOut, OptionOut } from "@naadi/shared";
import { uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerAssistantAsk } from "../assistant/ask";
import { pdfCanvasKind, pdfCanvasSchema } from "../canvas/kinds/pdf";
import { ingestErrorKey } from "./api";
import {
  batchSummary,
  confirmGroups,
  defaultOption,
  matchesFilter,
  metaLine,
  needsPolling,
  optionWording,
  STALE_MS,
  signalKey,
  sizeLabel,
  statusOf,
  type UploadState,
  vaultPrompts,
} from "./model";
import { AskAboutRegion, askAboutPdfRegion } from "./pdf/AskAboutRegion";
import { cropBox } from "./pdf/crop";
import { checkFiles, contentTypeFor, MB } from "./upload";

const t = (key: string, vars?: Readonly<Record<string, string>>) => uiText(null, key, vars);

const hooks = vi.hoisted(() => ({ confirm: vi.fn(), vault: vi.fn() }));
vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useConfirmPlacements: () => ({ mutateAsync: hooks.confirm, isPending: false, error: null }),
  useVaultDecision: () => ({ mutate: hooks.vault, isPending: false, error: null }),
}));
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (key: string, vars?: Record<string, string>) => uiText(null, key, vars),
}));

const NOW = Date.parse("2026-11-17T10:00:00Z");
let seq = 0;

function option(kind: string, preview: Record<string, unknown>, recommended = false, n = 0): OptionOut {
  return { option_id: `${kind}:${n}`, kind, recommended, preview };
}

function item(over: Partial<IngestItemOut> = {}): IngestItemOut {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    status: "ready",
    stage: "done",
    error: null,
    progress: 100,
    input_kind: "pdf",
    name: `notes-${seq}.pdf`,
    url: null,
    upload_id: `10000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    bytes: 2 * MB,
    pages: 12,
    batch_id: null,
    duplicate_of: null,
    duplicate: null,
    chapter_id: null,
    gate: null,
    options: [],
    placed: null,
    counts: null,
    retryable: false,
    created_at: new Date(NOW - 60_000 + seq * 1000).toISOString(),
    updated_at: new Date(NOW - 1000).toISOString(),
    ...over,
  };
}

const local = (over: Partial<UploadState> = {}): UploadState => ({
  isUploading: () => false,
  hasFailed: () => false,
  isAwaiting: () => false,
  ...over,
});

function waiting(batch: string | null, kinds: OptionOut[]): IngestItemOut {
  return item({ status: "needs_confirmation", stage: "confirm", batch_id: batch, options: kinds, progress: 90 });
}

describe("status words", () => {
  it("maps every server status and this tab's uploads to a worded status", () => {
    const up = local();
    expect(statusOf(item({ status: "received", stage: "receive" }), up)).toBe("received");
    expect(statusOf(item({ status: "extracting", stage: "extract" }), up)).toBe("reading");
    expect(statusOf(item({ status: "needs_confirmation", stage: "confirm" }), up)).toBe("needs_you");
    expect(statusOf(item({ status: "needs_confirmation", stage: "vault" }), up)).toBe("personal");
    expect(statusOf(item({ status: "placing", stage: "place" }), up)).toBe("placing");
    expect(statusOf(item({ status: "failed", stage: "done", error: "encrypted_pdf" }), up)).toBe("failed");
    expect(statusOf(item({ status: "vaulted", stage: "vaulted" }), up)).toBe("vaulted");
    const pending = item({ status: "received", stage: "upload" });
    expect(statusOf(pending, local({ isUploading: (id) => id === pending.id }))).toBe("uploading");
    expect(statusOf(pending, local({ hasFailed: (id) => id === pending.id }))).toBe("upload_failed");
    // Reserved but never uploaded from this tab (another device, a reload mid-upload).
    expect(statusOf(pending, up)).toBe("upload_incomplete");
    // Answered: shown as being placed until the run reports back.
    const answered = item({ status: "needs_confirmation", stage: "confirm" });
    expect(statusOf(answered, local({ isAwaiting: (i) => i.id === answered.id }))).toBe("placing");
  });

  it("has a ui_copy word for every status and a reason for every error code", () => {
    for (const code of [
      "uploading",
      "upload_failed",
      "upload_incomplete",
      "received",
      "reading",
      "needs_you",
      "personal",
      "placing",
      "ready",
      "failed",
      "vaulted",
    ]) {
      expect(t(`library.status.${code}`)).not.toBe(`library.status.${code}`);
    }
    expect(t(ingestErrorKey("zip_bomb"))).toMatch(/archive/i);
    expect(ingestErrorKey("something_new")).toBe("ingest.error.generic");
    expect(signalKey("passport_mrz")).toBe("ingest.signal.passport_mrz");
    expect(signalKey("retina_scan")).toBe("ingest.signal.other");
  });
});

describe("polling", () => {
  it("polls while something moves and stops when everything is terminal or waiting for the student", () => {
    const up = local();
    expect(needsPolling([item({ status: "extracting", stage: "extract" })], up, NOW)).toBe(true);
    expect(needsPolling([item({ status: "placing", stage: "place" })], up, NOW)).toBe(true);
    expect(needsPolling([item({ status: "received", stage: "receive" })], up, NOW)).toBe(true);
    const settled = [
      item(),
      item({ status: "failed", stage: "done", error: "extract_failed" }),
      item({ status: "vaulted", stage: "vaulted" }),
      waiting(null, [option("private_chapter", { title: "A" }, true)]),
      item({ status: "received", stage: "upload" }), // nobody is uploading it
    ];
    expect(needsPolling(settled, up, NOW)).toBe(false);
    expect(needsPolling([], up, NOW)).toBe(false);
  });

  it("gives up on a run that stopped reporting, but not on one the student just answered", () => {
    const stale = item({
      status: "extracting",
      stage: "extract",
      updated_at: new Date(NOW - STALE_MS - 1).toISOString(),
    });
    expect(needsPolling([stale], local(), NOW)).toBe(false);
    const answered = item({ status: "needs_confirmation", stage: "confirm" });
    expect(needsPolling([answered], local({ isAwaiting: () => true }), NOW)).toBe(true);
  });
});

describe("confirm cards", () => {
  it("groups waiting items by batch, newest group first, and keeps singles apart", () => {
    const a1 = waiting("b-1", [option("private_chapter", { title: "A" }, true)]);
    const a2 = waiting("b-1", [option("figure_overlay", { chapter_title: "Renal", heading: "Nephron" }, true)]);
    const single = waiting(null, [option("kb_entry", { title: "Link", words: 900 }, true)]);
    const lone = waiting("b-2", [option("flashcards", { deck: "Cards", count: 12 }, true)]);
    const done = item({ batch_id: "b-1" });
    const groups = confirmGroups([a1, a2, single, lone, done], local());
    expect(groups.map((g) => g.items.map((i) => i.id))).toEqual([[lone.id], [single.id], [a1.id, a2.id]]);
    expect(groups[2]?.batchId).toBe("b-1");
    // A batch with one item left waiting is confirmed through the item's own endpoint.
    expect(groups[0]?.batchId).toBeNull();
    expect(groups[1]?.batchId).toBeNull();
    // Answered items leave their card at once.
    expect(confirmGroups([a1, a2], local({ isAwaiting: (i) => i.id === a1.id }))[0]?.items).toEqual([a2]);
  });

  it("puts each personal document on its own Vault prompt", () => {
    const v1 = item({ status: "needs_confirmation", stage: "vault", batch_id: "b-3" });
    const v2 = item({ status: "needs_confirmation", stage: "vault", batch_id: "b-3" });
    expect(vaultPrompts([v1, v2, item()], local())).toEqual([v1, v2]);
    expect(confirmGroups([v1, v2], local())).toEqual([]);
  });

  it("preselects the recommended option, else the first", () => {
    const recommended = waiting(null, [option("kb_entry", {}, false, 0), option("private_chapter", {}, true, 1)]);
    expect(defaultOption(recommended)?.option_id).toBe("private_chapter:1");
    const none = waiting(null, [option("kb_entry", {}, false, 0), option("private_chapter", {}, false, 1)]);
    expect(defaultOption(none)?.option_id).toBe("kb_entry:0");
  });

  it("words every placement from its structured preview, never from server English", () => {
    const chapter = optionWording(
      t,
      option("private_chapter", { title: "Renal", sections: 4, pages: 12, figures: 1 }),
      "x.pdf",
    );
    expect(chapter).toEqual({ title: "New chapter: Renal", detail: "4 sections · 12 pages · 1 figure" });
    expect(optionWording(t, option("figure_overlay", { chapter_title: "Cardio", heading: "Valves" }), null).title).toBe(
      "Figure in Cardio › Valves",
    );
    expect(optionWording(t, option("flashcards", { deck: "Renal", count: 1 }), null)).toEqual({
      title: "Flashcards in “Renal”",
      detail: "1 card",
    });
    expect(optionWording(t, option("private_questions", { title: "Set", count: 5 }), null).detail).toBe("5 questions");
    expect(
      optionWording(t, option("kb_entry", { title: "Article", words: 900, url: "https://x.org" }), null).detail,
    ).toBe("900 words · From a link");
    expect(optionWording(t, option("notebook_page", { title: "Page", heading: "Loop of Henle" }), null).detail).toBe(
      "Linked to Loop of Henle",
    );
    // A kind registered later (S16/S17/S20) is worded generically, with the file name when there is no title.
    expect(optionWording(t, option("planner_import", { count: 3 }), "plan.csv")).toEqual({
      title: "Add “plan.csv”",
      detail: "3 items",
    });
  });

  it("summarises a batch per chosen kind", () => {
    const a = waiting("b", [option("private_chapter", { title: "A" }, true, 0), option("kb_entry", {}, false, 1)]);
    const b = waiting("b", [option("private_chapter", { title: "B" }, true, 0)]);
    const c = waiting("b", [option("figure_overlay", {}, true, 0)]);
    expect(batchSummary(t, [a, b, c], new Map())).toBe("2 files → a new chapter · 1 file → figures in a chapter");
    expect(batchSummary(t, [a, b], new Map([[a.id, "kb_entry:1"]]))).toBe(
      "1 file → your knowledge base · 1 file → a new chapter",
    );
  });
});

describe("filters and meta lines", () => {
  it("filters by kind and by what needs the student", () => {
    const link = item({ input_kind: "link" });
    const paste = item({ input_kind: "paste" });
    const pdf = item();
    const vault = item({ status: "needs_confirmation", stage: "vault" });
    const broken = item({ status: "failed", stage: "done" });
    const up = local();
    const all = [link, paste, pdf, vault, broken];
    expect(all.filter((i) => matchesFilter(i, "links", up))).toEqual([link]);
    expect(all.filter((i) => matchesFilter(i, "pastes", up))).toEqual([paste]);
    expect(all.filter((i) => matchesFilter(i, "files", up))).toEqual([pdf, vault, broken]);
    expect(all.filter((i) => matchesFilter(i, "needs_you", up))).toEqual([vault]);
    expect(all.filter((i) => matchesFilter(i, "failed", up))).toEqual([broken]);
    expect(all.filter((i) => matchesFilter(i, "all", up))).toEqual(all);
  });

  it("formats sizes without rounding the remaining space up", () => {
    expect(sizeLabel(t, 820 * 1024)).toBe("820 KB");
    expect(sizeLabel(t, 2.55 * MB)).toBe("2.5 MB");
    expect(sizeLabel(t, 600 * MB - 400 * 1024)).toBe("599.6 MB");
    expect(sizeLabel(t, 1.25 * 1024 * MB)).toBe("1.2 GB");
    expect(sizeLabel(t, null)).toBe("");
    expect(metaLine(t, item({ bytes: 2 * MB, pages: 12 }))).toBe("PDF · 2 MB · 12 pages");
  });
});

describe("upload content types and refusals", () => {
  const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
  it("sends an Anki package as application/vnd.anki even when the browser reports no type", () => {
    expect(contentTypeFor("Biology.apkg", "", zip)).toBe("application/vnd.anki");
    expect(contentTypeFor("collection.colpkg", "application/zip", zip)).toBe("application/vnd.anki");
    // Not really a ZIP: the extension lies, nothing is declared and the drop zone refuses it.
    expect(contentTypeFor("fake.apkg", "", new Uint8Array([1, 2, 3, 4]))).toBe("");
    expect(contentTypeFor("bundle.zip", "", zip)).toBe("application/zip");
  });

  it("falls back to the extension for HEIC photos and Markdown, and trusts a reported type", () => {
    expect(contentTypeFor("IMG_0001.HEIC", "", new Uint8Array())).toBe("image/heic");
    expect(contentTypeFor("notes.md", "", new Uint8Array())).toBe("text/markdown");
    expect(contentTypeFor("scan.pdf", "application/pdf", new Uint8Array())).toBe("application/pdf");
    expect(contentTypeFor("x.bin", "application/octet-stream", new Uint8Array())).toBe("application/octet-stream");
  });

  it("refuses too-big, empty and unaccepted files before uploading", async () => {
    const limits = { max_file_mb: 1, max_paste_chars: 1000, enabled: true, content_types: ["application/pdf"] };
    const small = new File(["%PDF-1.7"], "ok.pdf", { type: "application/pdf" });
    const big = new File([new Uint8Array(MB + 1)], "big.pdf", { type: "application/pdf" });
    const empty = new File([], "empty.pdf", { type: "application/pdf" });
    const exe = new File(["MZ"], "setup.exe", { type: "application/x-msdownload" });
    const out = await checkFiles([small, big, empty, exe], limits);
    expect(out.map((c) => c.refusal)).toEqual([null, "file_too_large", "empty_file", "unsupported_type"]);
    expect(t(ingestErrorKey("file_too_large"))).toBe("This is too big for the Library.");
  });
});

describe("pdf canvas kind", () => {
  it("is shaped like the S14 contract and validates its payload", () => {
    expect(pdfCanvasKind).toMatchObject({
      kind: "pdf",
      label_key: "canvas.kind.pdf",
      icon: "file-text",
      requires_flag: "library",
    });
    expect(typeof pdfCanvasKind.load).toBe("function");
    const id = "6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10";
    expect(pdfCanvasSchema.safeParse({ upload_id: id }).success).toBe(true);
    expect(pdfCanvasSchema.safeParse({ upload_id: id, page: 2 }).success).toBe(true);
    expect(pdfCanvasSchema.safeParse({ upload_id: id, page: 0 }).success).toBe(false);
    expect(pdfCanvasSchema.safeParse({ upload_id: id, page: 1.5 }).success).toBe(false);
    expect(pdfCanvasSchema.safeParse({ upload_id: "not-a-uuid" }).success).toBe(false);
    expect(t("canvas.kind.pdf")).toBe("PDF");
  });

  it("cuts the lassoed region out of the page canvas at its pixel density, clamped to the page", () => {
    const canvas = { width: 1600, height: 2000 };
    expect(cropBox({ left: 100, top: 50, width: 200, height: 100 }, canvas, 800)).toEqual({
      sx: 200,
      sy: 100,
      sw: 400,
      sh: 200,
      dw: 400,
      dh: 200,
    });
    expect(cropBox({ left: 700, top: 950, width: 400, height: 400 }, canvas, 800)).toMatchObject({ sw: 200, sh: 100 });
    expect(cropBox({ left: 900, top: 0, width: 10, height: 10 }, canvas, 800)).toBeNull();
    expect(cropBox({ left: 0, top: 0, width: 800, height: 1000 }, canvas, 800)).toMatchObject({ dw: 1280, dh: 1600 });
  });
});

describe("lasso → Ask", () => {
  let unregister: (() => void) | null = null;
  afterEach(() => {
    unregister?.();
    unregister = null;
  });

  it("is absent while no assistant is listening, and asks with the page surface when one is", async () => {
    const onAsk = vi.fn();
    const view = render(<AskAboutRegion visible busy={false} onAsk={onAsk} />);
    expect(screen.queryByRole("button", { name: "Ask about this" })).toBeNull();

    const handler = vi.fn();
    act(() => {
      unregister = registerAssistantAsk(handler);
    });
    await userEvent.click(screen.getByRole("button", { name: "Ask about this" }));
    expect(onAsk).toHaveBeenCalledTimes(1);
    view.rerender(<AskAboutRegion visible={false} busy={false} onAsk={onAsk} />);
    expect(screen.queryByRole("button", { name: "Ask about this" })).toBeNull();

    const canvas = document.createElement("canvas");
    const sent = await askAboutPdfRegion(
      "6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10:2",
      canvas,
      {
        left: 0,
        top: 0,
        width: 10,
        height: 10,
      },
      null,
    );
    expect(sent).toBe(true);
    expect(handler).toHaveBeenCalledWith({
      kind: "explain",
      context: { type: "pdf_page", id: "6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10:2", image: null },
    });
  });

  it("does nothing when no assistant is registered", async () => {
    const canvas = document.createElement("canvas");
    expect(await askAboutPdfRegion("x:1", canvas, { left: 0, top: 0, width: 1, height: 1 }, null)).toBe(false);
  });
});

describe("confirm and Vault cards", () => {
  beforeEach(() => {
    hooks.confirm.mockReset().mockResolvedValue(undefined);
    hooks.vault.mockReset();
  });

  function wrap(ui: ReactNode) {
    return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
  }

  it("confirms a batch in one call with the recommended choices, or the student's own", async () => {
    const { ConfirmCard } = await import("./ConfirmCard");
    const a = waiting("b-9", [
      option("private_chapter", { title: "Renal", sections: 3 }, true, 0),
      option("kb_entry", { title: "Renal", words: 500 }, false, 1),
    ]);
    const b = waiting("b-9", [option("figure_overlay", { chapter_title: "Renal", heading: "Nephron" }, true, 0)]);
    const [group] = confirmGroups([a, b], local());
    wrap(<ConfirmCard group={group as NonNullable<typeof group>} primary />);
    expect(screen.getByRole("heading", { name: "Where should these 2 files go?" })).toBeVisible();
    expect(screen.getByText("1 file → a new chapter · 1 file → figures in a chapter")).toBeVisible();
    expect(screen.getByRole("radio", { name: /New chapter: Renal/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Knowledge base: Renal/ })).not.toBeChecked();
    expect(screen.getAllByText("Recommended")).toHaveLength(2);
    // The one lime action of the view.
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toHaveAttribute("data-variant", "primary");

    await userEvent.click(screen.getByRole("radio", { name: /Knowledge base: Renal/ }));
    expect(screen.getByText("1 file → your knowledge base · 1 file → figures in a chapter")).toBeVisible();
    await userEvent.click(confirm);
    expect(hooks.confirm).toHaveBeenCalledWith({
      batchId: "b-9",
      choices: [
        { item_id: a.id, option_id: "kb_entry:1" },
        { item_id: b.id, option_id: "figure_overlay:0" },
      ],
      items: [a, b],
    });
  });

  it("asks before a personal document goes anywhere, wording its signals", async () => {
    const { VaultCard } = await import("./ConfirmCard");
    const doc = item({
      status: "needs_confirmation",
      stage: "vault",
      gate: { signals: ["passport_mrz", "doc_passport", "brand_new_signal"], doc_type: "passport" },
    });
    wrap(<VaultCard item={doc} primary={false} />);
    expect(screen.getByText("This looks like a personal document.")).toBeVisible();
    expect(screen.getByText("Store it in your Vault? Vault files are never sent to AI.")).toBeVisible();
    expect(screen.getByText("Passport machine-readable lines")).toBeVisible();
    expect(screen.getByText("Looks like a passport")).toBeVisible();
    expect(screen.getByText("Personal details")).toBeVisible();
    expect(screen.getByRole("button", { name: "Store in Vault" })).toHaveAttribute("data-variant", "strong");
    await userEvent.click(screen.getByRole("button", { name: "It's not personal" }));
    expect(hooks.vault).toHaveBeenCalledWith({ item: doc, vault: "continue" });
    await userEvent.click(screen.getByRole("button", { name: "Store in Vault" }));
    expect(hooks.vault).toHaveBeenLastCalledWith({ item: doc, vault: "vault" });
  });
});
