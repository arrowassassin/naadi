import type { ExportJob } from "@naadi/shared";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";

const h = vi.hoisted(() => ({
  online: true,
  jobs: [] as unknown[],
  listState: "ok" as "ok" | "pending" | "error",
  startError: null as unknown,
  startPending: false,
  start: vi.fn(),
  del: vi.fn(),
  deliver: vi.fn(async () => "download"),
}));
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (k: string, v?: Record<string, string>) => (v ? `${k} ${JSON.stringify(v)}` : k),
}));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => h.online }));
vi.mock("../pdf", () => ({ deliverPdf: h.deliver }));
vi.mock("./SettingsPage", () => ({ SettingsPage: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("./exportApi", () => ({
  useExportJobs: () => ({
    list: {
      data: h.listState === "ok" ? h.jobs : undefined,
      isPending: h.listState === "pending",
      error: h.listState === "error" ? new ApiError("x", 500, null, "rid-1") : null,
      isFetching: false,
      refetch: vi.fn(),
    },
    active: (h.jobs as ExportJob[]).find((j) => j.status === "queued" || j.status === "running"),
  }),
  useStartExport: () => ({ mutate: h.start, error: h.startError, isPending: h.startPending }),
  useDeleteExport: () => ({ mutate: h.del, isPending: false, isError: false }),
}));

import { DataExportPage } from "./DataExportPage";
import type { NotesPdfDeps } from "./notesPdf";

const job = (over: Partial<ExportJob> = {}): ExportJob => ({
  id: "j1",
  status: "ready",
  include_vault: false,
  parts: 1,
  bytes: 2048,
  created_at: "2026-11-17T10:00:00Z",
  expires_at: "2026-11-18T10:00:00Z",
  error: null,
  downloads: [{ part: 1, url: "https://files.example/one.zip" }],
  ...over,
});

const emptyDeps = (): NotesPdfDeps => ({
  fetchNotes: vi.fn(async () => []),
  fetchNotebooks: vi.fn(async () => []),
  fetchNotebook: vi.fn(),
  noteToPdf: vi.fn(),
  highlightsToPdf: vi.fn(),
  notebookToPdf: vi.fn(),
  merge: vi.fn(),
  t: (k) => k,
});

beforeEach(() => {
  Object.assign(h, { online: true, jobs: [], listState: "ok", startError: null, startPending: false });
  h.start.mockReset();
  h.del.mockReset();
  h.deliver.mockClear();
});

const cta = () => screen.getByRole("button", { name: /^export\.cta/ });

describe("Data & export", () => {
  it("shows the one CTA, the vault checkbox (off by default) and the empty list", () => {
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(cta()).toHaveTextContent("export.cta");
    expect(cta()).toBeEnabled();
    const box = screen.getByRole("checkbox", { name: "export.vault_label" });
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription("export.vault_note");
    expect(screen.getByText("export.jobs_empty")).toBeInTheDocument();
  });

  it("starts the job with include_vault from the checkbox", () => {
    render(<DataExportPage pdfDeps={emptyDeps} />);
    fireEvent.click(cta());
    expect(h.start.mock.calls[0]?.[0]).toBe(false);
    h.start.mockClear();
    fireEvent.click(screen.getByRole("checkbox", { name: "export.vault_label" }));
    fireEvent.click(cta());
    expect(h.start.mock.calls[0]?.[0]).toBe(true);
  });

  it("makes the notes PDFs in the same flow once the job started, and offers a cancel while preparing", async () => {
    let release: (v: never[]) => void = () => {};
    const deps = emptyDeps();
    deps.fetchNotes = vi.fn(() => new Promise<never[]>((r) => (release = r)));
    h.start.mockImplementation((_v: boolean, o: { onSuccess: () => void }) => o.onSuccess());
    render(<DataExportPage pdfDeps={() => deps} />);
    fireEvent.click(cta());
    expect(await screen.findByText("export.pdf_preparing")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "export.pdf_cancel" }));
    expect(await screen.findByText("export.pdf_cancelled")).toBeInTheDocument();
    release([]);
  });

  it("says in words when there is nothing to turn into PDFs", async () => {
    h.start.mockImplementation((_v: boolean, o: { onSuccess: () => void }) => o.onSuccess());
    render(<DataExportPage pdfDeps={emptyDeps} />);
    fireEvent.click(cta());
    expect(await screen.findByText("export.pdf_nothing")).toBeInTheDocument();
  });

  it("queued and running jobs read as words with a busy state and disable the CTA", () => {
    h.jobs = [job({ id: "a", status: "queued", downloads: [], bytes: null, expires_at: null })];
    const { unmount } = render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByText("export.status_queued")).toBeInTheDocument();
    expect(screen.getByText("export.queued_body")).toBeInTheDocument();
    expect(cta()).toBeDisabled();
    unmount();
    h.jobs = [job({ id: "a", status: "running", downloads: [], bytes: null, expires_at: null })];
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByText("export.status_running")).toBeInTheDocument();
    expect(screen.getByText("export.running_body")).toBeInTheDocument();
  });

  it("ready: a download link per part with size and absolute expiry; delete asks first", () => {
    h.jobs = [
      job({
        parts: 2,
        downloads: [
          { part: 1, url: "https://files.example/one.zip" },
          { part: 2, url: "https://files.example/two.zip" },
        ],
      }),
    ];
    render(<DataExportPage pdfDeps={emptyDeps} />);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "https://files.example/one.zip",
      "https://files.example/two.zip",
    ]);
    expect(links[0]).toHaveTextContent('export.part_label {"part":"1","parts":"2"}');
    expect(screen.getByText(/export\.total_size.*export\.available_until/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "export.delete" }));
    expect(h.del).not.toHaveBeenCalled();
    fireEvent.click(
      within(screen.getByText("export.delete_confirm").parentElement as HTMLElement).getByRole("button", {
        name: "export.delete_yes",
      }),
    );
    expect(h.del).toHaveBeenCalledWith("j1");
  });

  it("failed and expired jobs explain themselves; a new export is the CTA", () => {
    h.jobs = [
      job({ id: "f", status: "failed", error: "export_failed", downloads: [], created_at: "2026-11-17T11:00:00Z" }),
      job({ id: "e", status: "expired", downloads: [] }),
    ];
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByRole("alert")).toHaveTextContent("export.failed_generic");
    expect(screen.getByText("export.expired_body")).toBeInTheDocument();
    expect(cta()).toBeEnabled();
  });

  it.each([
    ["active", new ApiError("x", 409, "export_active", null), "export.start_active"],
    ["unavailable", new ApiError("x", 503, "export_unavailable", null), "export.start_unavailable"],
  ])("a refused start (%s) is worded", (kind, err, key) => {
    h.startError = err;
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByText(key)).toHaveAttribute("data-export-error", kind);
  });

  it("a 409 still makes the PDFs next to the job that is running", async () => {
    h.start.mockImplementation((_v: boolean, o: { onError: (e: unknown) => void }) =>
      o.onError(new ApiError("x", 409, "export_active", null)),
    );
    const deps = emptyDeps();
    render(<DataExportPage pdfDeps={() => deps} />);
    fireEvent.click(cta());
    await waitFor(() => expect(deps.fetchNotes).toHaveBeenCalled());
  });

  it("offline: the CTA is disabled with its reason", () => {
    h.online = false;
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(cta()).toBeDisabled();
    expect(cta()).toHaveAccessibleDescription("export.offline");
    fireEvent.click(cta());
    expect(h.start).not.toHaveBeenCalled();
  });

  it("loading and error states of the list", () => {
    h.listState = "pending";
    const { unmount } = render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByText("export.jobs_loading")).toBeInTheDocument();
    unmount();
    h.listState = "error";
    render(<DataExportPage pdfDeps={emptyDeps} />);
    expect(screen.getByRole("alert")).toHaveTextContent("rid-1");
    expect(screen.getByRole("button", { name: "settings.retry" })).toBeInTheDocument();
  });
});
