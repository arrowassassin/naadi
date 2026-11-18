import type { PathwayOut, StageOut, TaskOut } from "@naadi/shared";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  online: true,
  admin: false,
  pathway: null as unknown,
  costs: { items: [] as unknown[], currencies: ["USD", "INR"], max_entries: 2000 },
  vault: {
    items: [] as unknown[],
    types: ["passport", "other"],
    max_bytes: 20971520,
    max_documents: 100,
    expiry_soon_days: 60,
  },
  writes: [] as { id: string; fields: Record<string, unknown> }[],
  costWrites: [] as { op: string; fields?: unknown }[],
  verified: [] as string[],
}));

vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => state.online }));
vi.mock("../auth/api", () => ({ useMe: () => ({ data: { is_admin: state.admin, features: { pathway: true } } }) }));
vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  const q = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  });
  const mut = () => ({
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  });
  return {
    ...real,
    usePathwayData: () => q({ data: state.pathway, fromCache: false }),
    useCostsData: () => q({ data: state.costs, fromCache: false }),
    usePendingOps: () => [],
    usePathwaySync: () => ({ pending: 0, refused: [], flush: vi.fn() }),
    useTaskWriter: () => (id: string, fields: Record<string, unknown>) => state.writes.push({ id, fields }),
    useCostWriter: () => ({
      create: (f: unknown) => {
        state.costWrites.push({ op: "create", fields: f });
        return "id";
      },
      update: (_: string, f: unknown) => state.costWrites.push({ op: "update", fields: f }),
      remove: () => state.costWrites.push({ op: "remove" }),
    }),
    useSetStage: () => mut(),
    useVerifyTask: () => ({ ...mut(), mutate: (id: string) => state.verified.push(id) }),
    useOpenChecks: () => q({ items: [], open_count: 0 }),
    useVaultList: () => q(state.vault),
    useVaultActions: () => ({ patch: mut(), remove: mut(), open: mut() }),
    useUploadVault: () => mut(),
  };
});

import { PathwayPage } from "./PathwayPage";

const task = (id: string, over: Partial<TaskOut> = {}): TaskOut => ({
  id,
  sort: 1,
  title: `Task ${id}`,
  detail_md: "Detail with a [link](https://ecfmg.org/x).",
  due_rule: { kind: "anchor", anchor: "start", days: 7, date: null, month: null, day: null },
  cost_usd: 100,
  official_url: "https://www.ecfmg.org/page",
  official: true,
  last_verified: "2026-11-15",
  days_since_verified: 2,
  needs_review: false,
  reverify: null,
  status: "todo",
  due: null,
  notes: null,
  client_updated_at_ms: null,
  updated_at: null,
  ...over,
});

const stage = (key: string, title: string, tasks: TaskOut[]): StageOut => ({
  key,
  sort: 1,
  title,
  summary_md: "Summary [x](https://usmle.org)",
  official_links: [],
  last_verified: "2026-11-15",
  reverify: tasks.some((x) => x.reverify) ? "age" : null,
  done: tasks.filter((x) => x.status === "done" || x.status === "na").length,
  total: tasks.length,
  reverify_count: tasks.filter((x) => x.reverify).length,
  spend_planned_usd: 200,
  guide: { key, title: `${title} guide`, body_md: "# Guide\n\nText" },
  tasks,
});

function pathway(): PathwayOut {
  return {
    today: "2026-11-17",
    reverify_days: 90,
    current_stage: "s1",
    current_stage_source: "chosen",
    overview: null,
    stages: [
      stage("s1", "Eligibility", [
        task("a", { status: "done" }),
        task("b", { title: "Book the exam", reverify: "age", last_verified: "2026-08-01", days_since_verified: 108 }),
        task("c", { reverify: "flagged", needs_review: true }),
      ]),
      stage("s2", "Step 1", [task("d")]),
    ],
  };
}

beforeEach(() => {
  state.online = true;
  state.admin = false;
  state.pathway = pathway();
  state.costs = { items: [], currencies: ["USD", "INR"], max_entries: 2000 };
  state.vault = {
    items: [],
    types: ["passport", "other"],
    max_bytes: 20971520,
    max_documents: 100,
    expiry_soon_days: 60,
  };
  state.writes = [];
  state.costWrites = [];
  state.verified = [];
  window.HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  window.HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

describe("Pathway page", () => {
  it("lists the stages with the current one open, each task with when it was checked and its official link", () => {
    render(<PathwayPage />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Eligibility", "Step 1"]);
    const rows = screen.getAllByRole("listitem").filter((li) => li.hasAttribute("data-task"));
    expect(rows.map((r) => r.getAttribute("data-task"))).toEqual(["a", "b", "c"]); // Step 1 is closed
    const b = rows[1] as HTMLElement;
    expect(within(b).getByText("Checked 1 Aug 2026")).toBeInTheDocument();
    const link = within(b).getByRole("link", { name: /Official page/ });
    expect(link).toHaveAttribute("href", "https://www.ecfmg.org/page");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("shows Re-verify as a word with an icon on stale and flagged tasks, never on a fresh one", () => {
    render(<PathwayPage />);
    const row = (id: string) => document.querySelector(`[data-task="${id}"]`) as HTMLElement;
    expect(within(row("b")).getByText("Re-verify")).toBeInTheDocument();
    expect(within(row("c")).getByText("Re-verify")).toBeInTheDocument();
    expect(within(row("a")).queryByText("Re-verify")).toBeNull();
    expect(within(row("b")).getByText(/Last checked a long time ago/)).toBeInTheDocument();
    expect(within(row("c")).getByText(/The official page changed/)).toBeInTheDocument();
    expect(within(row("a")).getByText("Done")).toBeInTheDocument(); // status in words
    expect(within(row("b")).getByText("To do")).toBeInTheDocument();
  });

  it("has exactly one lime action: the next task of the current stage", () => {
    const { container } = render(<PathwayPage />);
    const lime = container.querySelectorAll("[data-variant='primary']");
    expect(lime).toHaveLength(1);
    expect(lime[0]).toHaveAttribute("data-cta", "next-task");
    expect(screen.getByText("Book the exam", { selector: "p" })).toBeInTheDocument();
    fireEvent.click(lime[0] as HTMLElement);
    expect(screen.getByRole("dialog", { name: "Book the exam" })).toBeInTheDocument();
  });

  it("marks a task done and back, writing to the device first", () => {
    render(<PathwayPage />);
    const row = document.querySelector('[data-task="b"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Mark done" }));
    expect(state.writes).toEqual([{ id: "b", fields: { status: "done" } }]);
    const done = document.querySelector('[data-task="a"]') as HTMLElement;
    fireEvent.click(within(done).getByRole("button", { name: "Mark to do" }));
    expect(state.writes[1]).toEqual({ id: "a", fields: { status: "todo" } });
  });

  it("filters to the tasks to re-verify", () => {
    render(<PathwayPage />);
    fireEvent.click(screen.getByRole("button", { name: "Show tasks to re-verify" }));
    const rows = screen.getAllByRole("listitem").filter((li) => li.hasAttribute("data-task"));
    expect(rows.map((r) => r.getAttribute("data-task"))).toEqual(["b", "c"]);
    expect(screen.queryByRole("heading", { level: 3, name: "Step 1" })).toBeNull();
    expect(screen.getByRole("button", { name: "Show all tasks" })).toHaveAttribute("aria-pressed", "true");
  });

  it("the task sheet shows the check strip, due rule, cost and notes; only an admin can mark it verified", () => {
    const { rerender } = render(<PathwayPage />);
    fireEvent.click(
      within(document.querySelector('[data-task="b"]') as HTMLElement).getByRole("button", { name: "Book the exam" }),
    );
    const sheet = screen.getByRole("dialog", { name: "Book the exam" });
    expect(within(sheet).getByText(/Checked against ecfmg\.org on 1 Aug 2026/)).toBeInTheDocument();
    expect(within(sheet).getByText(/7 days after you start/)).toBeInTheDocument();
    expect(within(sheet).getByText(/About \$100/)).toBeInTheDocument();
    expect(within(sheet).queryByRole("button", { name: "Mark verified" })).toBeNull();
    fireEvent.click(within(sheet).getByLabelText("Doing"));
    expect(state.writes.at(-1)).toEqual({ id: "b", fields: { status: "doing" } });
    state.admin = true;
    rerender(<PathwayPage />);
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Book the exam" })).getByRole("button", { name: "Mark verified" }),
    );
    expect(state.verified).toEqual(["b"]);
  });

  it("a link that is not on an official domain is labelled as a source with a warning", () => {
    state.pathway = {
      ...pathway(),
      stages: [
        stage("s1", "Eligibility", [task("x", { official: false, official_url: "https://blog.example.com/p" })]),
      ],
    };
    render(<PathwayPage />);
    const row = document.querySelector('[data-task="x"]') as HTMLElement;
    expect(within(row).getByRole("link", { name: /Source · blog\.example\.com/ })).toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: /Official page/ })).toBeNull();
  });
});

describe("Vault panel", () => {
  it("says files never go to AI, and shows the empty state", () => {
    render(<PathwayPage />);
    const vault = document.querySelector("[data-panel='vault']") as HTMLElement;
    expect(within(vault).getByText("Vault files are never sent to AI.")).toBeInTheDocument();
    expect(within(vault).getByText(/No documents yet/)).toBeInTheDocument();
    expect(within(vault).getByRole("button", { name: "Upload a file" })).toBeEnabled();
  });

  it("lists documents with an amber word for a near expiry, and offline disables upload and open with the reason", () => {
    state.vault.items = [
      {
        id: "d1",
        title: "Passport",
        doc_type: "passport",
        mime: "application/pdf",
        bytes: 2048,
        expires_on: "2026-12-17",
        expires_in_days: 30,
        expiry: "soon",
        created_at: "2026-11-01T00:00:00Z",
      },
      {
        id: "d2",
        title: "Diploma",
        doc_type: "other",
        mime: "image/png",
        bytes: 3000000,
        expires_on: null,
        expires_in_days: null,
        expiry: "none",
        created_at: "2026-11-01T00:00:00Z",
      },
    ];
    const { rerender } = render(<PathwayPage />);
    const vault = () => document.querySelector("[data-panel='vault']") as HTMLElement;
    expect(within(vault()).getByText("Expires in 30 days")).toBeInTheDocument();
    expect(within(vault()).getByText("Passport · 2 KB")).toBeInTheDocument();
    expect(within(vault()).getByRole("button", { name: "Open Passport" })).toBeEnabled();
    state.online = false;
    rerender(<PathwayPage />);
    expect(within(vault()).getByRole("button", { name: "Open Passport" })).toBeDisabled();
    expect(within(vault()).getByRole("button", { name: "Upload a file" })).toBeDisabled();
    expect(within(vault()).getByRole("button", { name: "Scan with camera" })).toBeDisabled();
    expect(within(vault()).getByText(/Offline: the vault needs a connection/)).toBeInTheDocument();
  });
});

describe("Cost tracker", () => {
  const cost = (id: string, over: Record<string, unknown>) => ({
    id,
    task_id: null,
    stage_key: "s1",
    label: "Exam fee",
    amount: "100.00",
    currency: "USD",
    paid_on: "2026-11-01",
    client_updated_at_ms: 1,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    pending: false,
    ...over,
  });

  it("shows empty, then totals per currency and per stage without converting", () => {
    const { rerender } = render(<PathwayPage />);
    expect(document.querySelector("[data-empty='costs']")).not.toBeNull();
    state.costs.items = [
      cost("1", {}),
      cost("2", { label: "Bank draft", amount: "8000.00", currency: "INR" }),
      cost("3", { label: "Seat", stage_key: "s2", paid_on: null, amount: "50.00" }),
    ];
    rerender(<PathwayPage />);
    const panel = document.querySelector("[data-panel='costs']") as HTMLElement;
    const paid = [...panel.querySelectorAll("[data-total='paid']")].map((n) => n.textContent);
    expect(paid).toEqual(["₹8,000", "$100"]);
    expect(within(panel).getByText(/still to pay: \$50/)).toBeInTheDocument();
    const stageOne = panel.querySelector("[data-cost-stage='s1']") as HTMLElement;
    expect(within(stageOne).getByText(/paid ₹8,000 · paid \$100/)).toBeInTheDocument();
    expect(
      within(panel.querySelector("[data-cost-stage='s2']") as HTMLElement).getByText("Not paid yet"),
    ).toBeInTheDocument();
  });

  it("adds an entry through the form, writing it to the device first", () => {
    render(<PathwayPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add a cost" }));
    const sheet = screen.getByRole("dialog", { name: "Add a cost" });
    fireEvent.change(within(sheet).getByLabelText("What it is for"), { target: { value: "Prometric seat" } });
    fireEvent.change(within(sheet).getByLabelText("Amount"), { target: { value: "210.5" } });
    fireEvent.change(within(sheet).getByLabelText("Currency"), { target: { value: "INR" } });
    fireEvent.change(within(sheet).getByLabelText("Paid on (optional)"), { target: { value: "2026-11-10" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Save cost" }));
    expect(state.costWrites).toEqual([
      {
        op: "create",
        fields: { label: "Prometric seat", amount: 210.5, currency: "INR", paid_on: "2026-11-10", stage_key: "s1" },
      },
    ]);
  });

  it("refuses an empty label or a missing amount with a message", () => {
    render(<PathwayPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add a cost" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Add a cost" })).getByRole("button", { name: "Save cost" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Enter what it is for");
    expect(state.costWrites).toEqual([]);
  });
});
