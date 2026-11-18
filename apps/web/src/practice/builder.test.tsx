import { type PoolResponse, uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { BuilderPage } from "./BuilderPage";
import { parsePracticeSearch } from "./routes";

// The data hooks are the boundary (the API itself is covered by the API and e2e suites).
const h = vi.hoisted(() => ({
  examAvailable: true,
  create: { mutate: vi.fn(), isPending: false, error: null as Error | null },
  mock: { mutate: vi.fn(), isPending: false, error: null as Error | null, variables: undefined as string | undefined },
  mocks: {} as Record<string, string[]>,
}));
vi.mock("./api", () => ({
  usePool: (f: { exam?: string }) => ({ data: pool(f.exam ?? "step1", h.examAvailable), isPending: false }),
  useBlocks: () => ({ data: { blocks: [] } }),
  useCreateBlock: () => h.create,
  useCreateWeakSpotBlock: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));
vi.mock("./timed", async (orig) => {
  const real = await orig<typeof import("./timed")>();
  return {
    ...real,
    useCreateMockBlock: () => h.mock,
  };
});
vi.mock("./copy", () => ({
  useCopy:
    () =>
    (key: string, vars: Record<string, string | number> = {}) =>
      uiText(null, key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))),
}));

const t = (key: string, vars: Record<string, string | number> = {}) =>
  uiText(null, key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)])));

function pool(exam: string, examAvailable = true): PoolResponse {
  return {
    exam,
    exams: [
      { id: "step1", label: "Step 1", mock_ids: h.mocks.step1 ?? [], blocks: 7, minutes_per_block: 60 },
      { id: "step2ck", label: "Step 2 CK", mock_ids: h.mocks.step2ck ?? [], blocks: 8, minutes_per_block: 60 },
    ],
    timing: { break_minutes: 45, seconds_per_question: { tutor: null, timed: 90, exam: 90 } },
    mocks: (h.mocks[exam] ?? []).map((id) => ({ id, title: `Mock ${id}`, blocks: 2, questions: 80 })),
    modes: [
      { id: "tutor", available: true },
      { id: "timed", available: examAvailable },
      { id: "exam", available: examAvailable },
    ],
    systems: [{ id: "renal", label: "Renal", count: 90 }],
    disciplines: [],
    status: { all: 100, unused: 100, incorrect: 0, flagged: 0, correct: 0 },
    difficulty: { all: 100 },
    limits: { min_questions: 1, max_questions: 40, default_questions: 40 },
    total: 100,
  };
}

function renderBuilder(url: string) {
  const root = createRootRoute({ component: Outlet });
  const builder = createRoute({
    getParentRoute: () => root,
    path: "/practice",
    validateSearch: parsePracticeSearch,
    component: () => {
      const { exam, mock } = builder.useSearch();
      return <BuilderPage key={exam ?? ""} exam={exam} mock={mock} />;
    },
  });
  const block = createRoute({
    getParentRoute: () => root,
    path: "/practice/blocks/$blockId/$n",
    component: () => <p>exam view</p>,
  });
  const router = createRouter({
    routeTree: root.addChildren([builder, block]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  h.examAvailable = true;
  h.create.mutate.mockReset();
  h.mock.mutate.mockReset();
  h.create.error = null;
  h.mock.error = null;
  h.mocks = { step1: ["mock1"], step2ck: ["mock2"] };
});

describe("Builder: blocks of a timed or exam session", () => {
  it("shows the Blocks chips only for timed and exam, and sends `sections`", async () => {
    renderBuilder("/practice");
    await screen.findByRole("button", { name: t("practice.start") });
    expect(screen.queryByRole("group", { name: t("practice.sections_group") })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: t("practice.mode_timed") }));
    const group = screen.getByRole("group", { name: t("practice.sections_group") });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["1", "2", "3", "4"]);
    // 100 questions match: two blocks of 40 fit, three do not.
    expect(chips[2]).toHaveAttribute("aria-disabled", "true");
    expect(chips[2]).toHaveAccessibleDescription(t("practice.sections_not_enough"));
    await userEvent.click(chips[1] as HTMLElement);
    expect(chips[1]).toHaveAttribute("aria-pressed", "true");
    expect(group).toHaveTextContent(t("practice.sections_hint", { sections: 2, count: 40, minutes: 60 }));
    await userEvent.click(screen.getByRole("button", { name: t("practice.start") }));
    expect(h.create.mutate.mock.calls[0]?.[0]).toMatchObject({ mode: "timed", count: 40, sections: 2 });
  });

  it("tutor blocks send no sections", async () => {
    renderBuilder("/practice");
    await userEvent.click(await screen.findByRole("button", { name: t("practice.start") }));
    expect(h.create.mutate.mock.calls[0]?.[0]).not.toHaveProperty("sections");
  });
});

describe("Builder: mock exams", () => {
  it("lists the selected exam's mock as a secondary action that starts it and opens question 1", async () => {
    const router = renderBuilder("/practice");
    const group = await screen.findByRole("group", { name: t("practice.mock_group") });
    const start = within(group).getByRole("button", { name: /Mock mock1/ });
    expect(start.className).not.toMatch(/\bbg-lime\b/);
    expect(within(group).queryByRole("button", { name: /mock2/ })).toBeNull();
    await userEvent.click(start);
    expect(h.mock.mutate).toHaveBeenCalledWith("mock1", expect.anything());
    const opts = h.mock.mutate.mock.calls[0]?.[1] as { onSuccess: (b: { id: string }) => void };
    opts.onSuccess({ id: "6f1c1b7e-8a2d-4c3e-9f10-2b3c4d5e6f70" });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/practice/blocks/6f1c1b7e-8a2d-4c3e-9f10-2b3c4d5e6f70/1"),
    );
  });

  it("is disabled with its reason while exam mode is off", async () => {
    h.examAvailable = false;
    renderBuilder("/practice");
    const group = await screen.findByRole("group", { name: t("practice.mock_group") });
    const start = within(group).getByRole("button", { name: /Mock mock1/ });
    expect(start).toHaveAttribute("aria-disabled", "true");
    expect(start).toHaveAccessibleDescription(t("practice.mock_unavailable"));
    await userEvent.click(start);
    expect(h.mock.mutate).not.toHaveBeenCalled();
  });

  it("a `?mock=` link selects the mock's exam and focuses it without starting it", async () => {
    const router = renderBuilder("/practice?mock=mock2");
    await waitFor(() => expect(router.state.location.search).toEqual({ exam: "step2ck", mock: "mock2" }));
    const start = await screen.findByRole("button", { name: /Mock mock2/ });
    await waitFor(() => expect(start).toHaveFocus());
    expect(h.mock.mutate).not.toHaveBeenCalled();
  });

  it("shows the ui_copy text for a mock the server does not have", async () => {
    h.mock.error = new ApiError("This mock exam is not available.", 404, "mock_not_found", null);
    renderBuilder("/practice");
    expect(await screen.findByRole("alert")).toHaveTextContent(t("practice.mock_not_found"));
  });
});
