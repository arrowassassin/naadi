import { uiText, type WeekOut } from "@naadi/shared";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { PlannerPage } from "./PlannerPage";

const h = vi.hoisted(() => ({
  week: { data: undefined, isError: false, error: null, isFetching: false, refetch: vi.fn() } as Record<
    string,
    unknown
  >,
  online: true,
  setStatus: { mutate: vi.fn(), isError: false },
  start: { mutate: vi.fn(), isPending: false },
  decide: { mutate: vi.fn(), isPending: false },
  run: vi.fn(),
  laptop: false,
}));
vi.mock("./api", () => ({
  usePlanWeek: () => h.week,
  useSetItemStatus: () => h.setStatus,
  useStartRebalance: () => h.start,
  useDecideRebalance: () => h.decide,
  useFlushPlanOutbox: () => undefined,
}));
vi.mock("../today/useRunAction", () => ({ useRunAction: () => ({ run: h.run, pending: false, failed: false }) }));
vi.mock("../auth/api", () => ({ useMe: () => ({ data: { features: { cards: true, planner: true } } }) }));
vi.mock("../practice/api", () => ({ useUserId: () => "u1" }));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => h.online }));
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (key: string, vars?: Record<string, string>) => uiText(null, key, vars),
}));

const item = (over: Record<string, unknown>) => ({
  id: "i1",
  day: "2026-11-18",
  kind: "read",
  title: "Read renal physiology",
  ref_type: "chapter",
  ref_id: "ch05",
  target_minutes: 60,
  target_count: null,
  status: "planned",
  origin: "generator",
  sort: 0,
  plan_day: 3,
  behind: false,
  moved_from: null,
  client_updated_at_ms: null,
  ...over,
});

function day(date: string, n: number, over: Record<string, unknown> = {}) {
  return {
    date,
    weekday: "Mon",
    day_no: n,
    phase: null,
    chapter_ref: null,
    topic: null,
    target_hours: 8,
    planned_hours: 1,
    assessment: false,
    rest: false,
    extra: false,
    in_plan: true,
    day_off: false,
    is_today: false,
    past: false,
    items: [],
    ...over,
  };
}

function weekFixture(over: Record<string, unknown> = {}): WeekOut {
  const next = item({ id: "next", title: "Read acid-base", day: "2026-11-18" });
  return {
    plan_id: "p1",
    start: "2026-11-16",
    end: "2026-11-22",
    week: 1,
    weeks: 9,
    today: "2026-11-18",
    exam: "Step 1",
    exam_date: "2027-01-15",
    days_to_exam: 58,
    prev_start: null,
    next_start: "2026-11-23",
    hours_planned: 12,
    hours_target: 40,
    behind: 1,
    days: [
      day("2026-11-16", 1, {
        past: true,
        items: [item({ id: "b1", day: "2026-11-16", title: "Read missed", behind: true })],
      }),
      day("2026-11-17", 2, { past: true }),
      day("2026-11-18", 3, { is_today: true, items: [next] }),
      day("2026-11-19", 4, {
        assessment: true,
        items: [item({ id: "a1", kind: "assessment", title: "NBME 1", ref_type: "external" })],
      }),
      day("2026-11-20", 5),
      day("2026-11-21", 6),
      day("2026-11-22", 7),
    ],
    milestones: [
      { kind: "assessment", date: "2026-11-19", day_no: 4, title: "NBME 1", days_away: 1 },
      { kind: "exam", date: "2027-01-15", day_no: null, title: null, days_away: 58 },
    ],
    next_item: next,
    rebalance: null,
    ...over,
  } as unknown as WeekOut;
}

const proposal = {
  id: "r1",
  created_at: "2026-11-18T00:00:00Z",
  trigger: "coach",
  status: "proposed",
  applied_at: null,
  thread_id: null,
  proposal: {
    moves: [
      {
        item_id: "b1",
        from_day: "2026-11-16",
        to_day: "2026-11-20",
        sort: 0,
        kind: "read",
        title: "Read missed",
        minutes: 60,
      },
    ],
    drops: [{ item_id: "d1", day: "2026-11-21", reason: "review", kind: "read", title: "Mixed review" }],
    warnings: [{ code: "unplaced", count: 3, item_ids: [] }],
    missed: 4,
    spilled: 0,
    fingerprint: "fp",
  },
};

async function mount() {
  const root = createRootRoute({ component: Outlet });
  const plan = createRoute({ getParentRoute: () => root, path: "/plan", component: PlannerPage });
  const router = createRouter({
    routeTree: root.addChildren([plan]),
    history: createMemoryHistory({ initialEntries: ["/plan"] }),
  });
  await router.load();
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  h.online = true;
  h.week = {
    data: { data: weekFixture(), fromCache: false },
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  };
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
});

describe("planner page states", () => {
  it("shows a skeleton while loading, with an announced status", async () => {
    h.week = { data: undefined, isError: false, error: null, isFetching: true, refetch: vi.fn() };
    await mount();
    expect(await screen.findByRole("status")).toHaveTextContent("Loading your plan");
  });
  it("sends a student without a plan to onboarding", async () => {
    h.week = {
      data: { data: null, fromCache: false },
      isError: false,
      error: null,
      isFetching: false,
      refetch: vi.fn(),
    };
    await mount();
    expect(await screen.findByRole("heading", { name: "No plan yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set up your plan" })).toHaveAttribute("href", "/welcome");
  });
  it("shows an error with a retry", async () => {
    const refetch = vi.fn();
    h.week = {
      data: undefined,
      isError: true,
      error: new ApiError("down", null, null, null),
      isFetching: false,
      refetch,
    };
    await mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your plan could not be loaded");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalled();
  });
  it("says it is offline and explains why Rebalance is off", async () => {
    h.online = false;
    await mount();
    expect(await screen.findByTestId("offline-note")).toHaveTextContent("You are offline");
    expect(screen.getByRole("button", { name: "Rebalance" })).toBeDisabled();
    expect(screen.getAllByText("Rebalancing needs a connection.").length).toBeGreaterThan(0);
  });
});

describe("planner page content", () => {
  it("has exactly one primary (lime) action: today's next item, and it starts the item", async () => {
    await mount();
    const start = await screen.findByTestId("cta-start");
    expect(document.querySelectorAll("button.bg-lime")).toHaveLength(1);
    await userEvent.click(start);
    expect(h.run).toHaveBeenCalledWith({ kind: "start_reading", chapterId: "ch05" });
  });
  it("marks behind items and assessment days with words and icons, never colour alone", async () => {
    await mount();
    expect((await screen.findAllByText("Behind")).length).toBeGreaterThan(0);
    // the chosen day (today) is listed; the strip flags the assessment day for assistive tech
    const strip = screen.getByRole("list", { name: "Days of the week" });
    expect(within(strip).getByText("Assessment")).toBeInTheDocument();
    expect(within(strip).getByText("Behind")).toBeInTheDocument();
    await userEvent.click(within(strip).getByRole("button", { name: /19/ }));
    const list = screen.getByTestId("day-list");
    expect(within(list).getAllByText("Assessment").length).toBeGreaterThan(0);
    expect(list.querySelectorAll("svg").length).toBeGreaterThan(0);
  });
  it("opens an item and marks it done", async () => {
    await mount();
    await userEvent.click(await screen.findByRole("button", { name: /Read acid-base/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Mark done" }));
    expect(h.setStatus.mutate).toHaveBeenCalledWith({ id: "next", status: "done" });
  });
  it("lists the road to exam day", async () => {
    await mount();
    expect(await screen.findByText("Road to exam day")).toBeInTheDocument();
    expect(screen.getByText("Assessment: NBME 1")).toBeInTheDocument();
    expect(screen.getByText("Step 1 exam day")).toBeInTheDocument();
  });
  it("renders the seven-column calendar on a laptop", async () => {
    window.matchMedia = ((q: string) => ({
      matches: true,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    await mount();
    const grid = await screen.findByTestId("week-grid");
    expect(within(grid).getAllByRole("listitem").length).toBeGreaterThanOrEqual(7);
    expect(grid.querySelectorAll("[data-today]")).toHaveLength(1);
    expect(grid.querySelectorAll("[data-assessment]")).toHaveLength(1);
  });
});

describe("rebalance suggestion", () => {
  beforeEach(() => {
    h.week = {
      data: { data: weekFixture({ rebalance: proposal }), fromCache: false },
      isError: false,
      error: null,
      isFetching: false,
      refetch: vi.fn(),
    };
  });
  it("is violet, with Accept / Show changes / Decline, and says what would happen", async () => {
    await mount();
    const card = await screen.findByTestId("rebalance-card");
    expect(within(card).getByText("Catch up on 4 missed items")).toBeInTheDocument();
    expect(within(card).getByText("1 item moves")).toBeInTheDocument();
    expect(within(card).getByText("1 review item dropped")).toBeInTheDocument();
    expect(within(card).getByText("3 items don't fit before your exam")).toBeInTheDocument();
    expect(card.className).toContain("bg-violet-ground");
    expect(card.querySelector(".bg-lime")).toBeNull();
    expect(document.querySelectorAll("button.bg-lime")).toHaveLength(1); // still only the next-up action
  });
  it("Show changes lists each move, drop and warning", async () => {
    await mount();
    const card = await screen.findByTestId("rebalance-card");
    expect(within(card).queryByTestId("diff-moves")).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "Show changes" }));
    expect(within(card).getByTestId("diff-moves")).toHaveTextContent("Read missed: 16 Nov → 20 Nov");
    expect(within(card).getByTestId("diff-drops")).toHaveTextContent("Mixed review (21 Nov)");
    expect(within(card).getByTestId("diff-warnings")).toHaveTextContent("3 items don't fit before your exam");
    expect(within(card).getByRole("button", { name: "Hide changes" })).toHaveAttribute("aria-expanded", "true");
  });
  it("Accept and Decline hand the decision to the planner run; nothing else changes the plan", async () => {
    await mount();
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(h.decide.mutate).toHaveBeenCalledWith(expect.objectContaining({ decision: "accept" }), expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(h.decide.mutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ decision: "decline" }),
      expect.anything(),
    );
    expect(h.setStatus.mutate).not.toHaveBeenCalled();
  });
  it("cannot be answered offline, and says why", async () => {
    h.online = false;
    await mount();
    expect(await screen.findByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Decline" })).toBeDisabled();
  });
  it("tells the student what happened after Accept", async () => {
    h.decide.mutate = vi.fn((_v, opts) =>
      opts.onSuccess({ kind: "done", outcome: { outcome: "applied", detail: { moved: 5, dropped: 2 } } }),
    );
    await mount();
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByTestId("planner-notice")).toHaveTextContent("Plan updated: 5 moved, 2 dropped.");
  });
});
