import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { REQUIRED_UI_COPY_KEYS, type TrendPoint, uiText, type WeightOverlay } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../auth/apiError";
import { parsePracticeSearch } from "../../practice/routes";
import { maxSections, sectionsThatFit, startableMocks } from "../../practice/timed";
import { BLOCK_ID, QUESTIONS, questionView, RESULTS } from "./fixtures";
import {
  filterCounts,
  filterList,
  insightText,
  landingFor,
  neighbours,
  previousScore,
  reviewKey,
  trendGeometry,
  weightGeometry,
  weightScale,
} from "./model";
import { questionLabel, ResultsPage } from "./ResultsPage";
import { parseReviewSearch, ReviewPage } from "./ReviewPage";

// The data hooks are the boundary: the API contract is covered by the API tests and the e2e suite.
const hooks = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  question: (_n: number) => ({}) as Record<string, unknown>,
  block: {} as Record<string, unknown>,
}));
vi.mock("./api", () => ({
  useBlockResults: () => hooks.results,
  useReviewQuestion: (_b: string, n: number, enabled: boolean) =>
    enabled ? hooks.question(n) : { data: undefined, error: null },
  isFinal: (e: unknown) => e instanceof ApiError && e.status !== null && e.status < 500 && e.status !== 429,
}));
vi.mock("../../practice/api", () => ({
  useBlock: () => hooks.block,
  useUserId: () => "user-1",
}));
// The wording is the shipped default (what the screen shows before the config loads).
vi.mock("../../practice/copy", () => ({
  useCopy:
    () =>
    (key: string, vars: Record<string, string | number> = {}) =>
      uiText(null, key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))),
}));

const t = (key: string, vars: Record<string, string | number> = {}) =>
  uiText(null, key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)])));

function renderAt(url: string) {
  const root = createRootRoute({ component: Outlet });
  const practice = createRoute({ getParentRoute: () => root, path: "/practice", component: () => <p>builder</p> });
  const exam = createRoute({
    getParentRoute: () => root,
    path: "/practice/blocks/$blockId/$n",
    component: () => <p>exam view</p>,
  });
  const results = createRoute({
    getParentRoute: () => root,
    path: "/practice/blocks/$blockId/results",
    component: () => <ResultsPage blockId={results.useParams().blockId} />,
  });
  const review = createRoute({
    getParentRoute: () => root,
    path: "/practice/blocks/$blockId/review/$n",
    validateSearch: parseReviewSearch,
    params: {
      parse: (p: Record<string, string>) => ({ blockId: p.blockId as string, n: Number(p.n) }),
      stringify: (p: { blockId: string; n: number }) => ({ blockId: p.blockId, n: String(p.n) }),
    },
    component: () => {
      const { blockId, n } = review.useParams();
      return <ReviewPage blockId={blockId} n={n} filter={review.useSearch().filter} />;
    },
  });
  const router = createRouter({
    routeTree: root.addChildren([practice, exam, results, review]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  hooks.results = { data: RESULTS, error: null, isFetching: false, refetch: vi.fn() };
  hooks.question = (n) => ({ data: questionView(n), error: null, refetch: vi.fn() });
  hooks.block = { data: { next_n: 17 } };
});

describe("review lists", () => {
  it("builds each filter's question numbers and counts", () => {
    expect(filterList(QUESTIONS, "missed")).toEqual([3, 7, 8, 12, 15, 19, 22, 27, 31, 34, 38]);
    expect(filterList(QUESTIONS, "flagged")).toEqual([5, 12, 20, 33]);
    expect(filterList(QUESTIONS, "changed")).toEqual([7, 9, 22]);
    expect(filterList(QUESTIONS, "all")).toHaveLength(40);
    expect(filterCounts(QUESTIONS)).toEqual({ all: 40, missed: 11, flagged: 4, changed: 3 });
  });

  it("finds previous and next around a question, in the list or not", () => {
    const list = [3, 7, 8, 12];
    expect(neighbours(list, 7)).toEqual({ prev: 3, next: 8, index: 2 });
    expect(neighbours(list, 3)).toEqual({ prev: null, next: 7, index: 1 });
    expect(neighbours(list, 12)).toEqual({ prev: 8, next: null, index: 4 });
    expect(neighbours(list, 10)).toEqual({ prev: 8, next: 12, index: null });
    expect(neighbours([], 5)).toEqual({ prev: null, next: null, index: null });
    expect(landingFor(list, 8)).toBe(8);
    expect(landingFor(list, 9)).toBe(3);
    expect(landingFor([], 9)).toBeNull();
  });

  it("parses the filter and the keys", () => {
    expect(parseReviewSearch({ filter: "missed" })).toEqual({ filter: "missed" });
    expect(parseReviewSearch({ filter: "nope" })).toEqual({ filter: "all" });
    expect(parseReviewSearch({})).toEqual({ filter: "all" });
    const k = (key: string, mods = {}) => ({
      key,
      metaKey: false,
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      ...mods,
    });
    expect(reviewKey(k("n"))).toBe("next");
    expect(reviewKey(k("N"))).toBe("next");
    expect(reviewKey(k("ArrowRight"))).toBe("next");
    expect(reviewKey(k("ArrowLeft"))).toBe("prev");
    expect(reviewKey(k("ArrowLeft", { altKey: true }))).toBeNull(); // browser back
    expect(reviewKey(k("n", { metaKey: true }))).toBeNull();
    expect(reviewKey(k("f"))).toBeNull();
  });
});

describe("trend geometry", () => {
  const p = (score: number, current = false): TrendPoint => ({
    block_id: `b${score}`,
    finished_at: "2026-11-01T00:00:00Z",
    score,
    mode: "timed",
    current,
  });

  it("spaces points evenly and maps 0-100 % onto the inner height", () => {
    const g = trendGeometry([p(0), p(50), p(100, true)], 600, 180, 16);
    expect(g.dots.map((d) => d.x)).toEqual([16, 300, 584]);
    expect(g.dots.map((d) => d.y)).toEqual([164, 90, 16]);
    expect(g.path).toBe("M16 164 L300 90 L584 16");
    expect(g.dots[2]?.current).toBe(true);
  });

  it("centres a single point and clamps scores outside 0-100", () => {
    expect(trendGeometry([p(72)], 600, 180, 16).dots[0]?.x).toBe(300);
    const g = trendGeometry([p(-5), p(130)], 100, 100, 0);
    expect(g.dots.map((d) => d.y)).toEqual([100, 0]);
    expect(trendGeometry([], 600, 180, 16)).toEqual({ path: "", dots: [] });
  });

  it("the previous score is the point before this block", () => {
    expect(previousScore(RESULTS.trend)).toBe(66);
    expect(previousScore([p(70, true)])).toBeNull();
    expect(previousScore([p(40), p(70)])).toBe(40); // no current flag: the last one is this block
  });
});

describe("official weight geometry", () => {
  const w = (o: Partial<WeightOverlay>): WeightOverlay => ({
    id: "s",
    label: "S",
    weight_min: 6,
    weight_max: 10,
    share: 8,
    total: 4,
    correct: 2,
    percent: 50,
    ...o,
  });

  it("scales to the largest band or share, rounded up to 5", () => {
    expect(weightScale([w({ weight_max: 13, share: 22 })])).toBe(25);
    expect(weightScale([w({ weight_max: 10, share: 3 })])).toBe(10);
    expect(weightScale([])).toBe(5);
  });

  it("places the band and the share on the same axis and says where the share falls", () => {
    expect(weightGeometry(w({}), 20)).toEqual({ band: { x: 30, w: 20 }, share: 40, place: "within" });
    expect(weightGeometry(w({ share: 2 }), 20).place).toBe("below");
    expect(weightGeometry(w({ share: 25 }), 20)).toMatchObject({ share: 100, place: "above" });
    expect(weightGeometry(w({ weight_min: 8, weight_max: 8 }), 20).band).toEqual({ x: 40, w: 0.75 });
    expect(weightGeometry(w({ weight_min: null, weight_max: null }), 20)).toEqual({
      band: null,
      share: 40,
      place: null,
    });
    expect(weightGeometry(w({ weight_min: 7, weight_max: null, share: 7 }), 20).place).toBe("within");
  });
});

describe("insight sentences", () => {
  it("fills the ui_copy template with the server's values", () => {
    expect(insightText(t, { kind: "changes_hurt", vars: { lost: 2, gained: 1 } })).toBe(
      t("results.insight.changes_hurt", { lost: "2", gained: "1" }),
    );
    expect(
      insightText(t, {
        kind: "underweighted",
        vars: { topic: "Cardio", percent: 40, weight_min: 6, weight_max: 10.5 },
      }),
    ).toContain("6–10.5");
  });

  it("drops a sentence with a missing value or a template the config lacks", () => {
    expect(insightText(t, { kind: "trend_up", vars: { delta: 7 } })).toBeNull();
    expect(insightText((k) => k, { kind: "pace_slow", vars: { mean: 100, target: 90 } })).toBeNull();
    expect(insightText(() => "  ", { kind: "pace_slow", vars: { mean: 100, target: 90 } })).toBeNull();
  });

  it("every insight kind has a template", () => {
    const kinds = [
      "weakest_topic",
      "strongest_topic",
      "pace_slow",
      "pace_fast",
      "changes_hurt",
      "changes_helped",
      "flags_right",
      "underweighted",
      "trend_up",
      "trend_down",
    ];
    for (const k of kinds) expect(REQUIRED_UI_COPY_KEYS).toContain(`results.insight.${k}`);
  });
});

describe("Results page", () => {
  it("shows the score, the sentence, the tiles and one lime action", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent(t("results.score_sr", { percent: 73 }));
    expect(screen.getByText(/^29 of 40\./)).toHaveTextContent(t("results.sentence_up", { delta: 7 }));
    const stats = screen.getByRole("region", { name: t("results.stats_label") });
    for (const label of [
      "results.stat_correct",
      "results.stat_time",
      "results.stat_changed",
      "results.stat_flagged",
      "results.stat_omitted",
    ]) {
      expect(within(stats).getByText(t(label))).toBeInTheDocument();
    }
    expect(within(stats).getByText(t("results.stat_seconds", { seconds: 82 }))).toBeInTheDocument();
    expect(within(stats).getByText(t("results.stat_time_note", { target: 90 }))).toBeInTheDocument();
    expect(within(stats).getByText(t("results.stat_changed_note", { n: 2 }))).toBeInTheDocument();
    expect(within(stats).getByText(t("results.stat_flagged_note", { n: 3 }))).toBeInTheDocument();

    const cta = screen.getByRole("link", { name: t("results.review_misses", { n: 11 }) });
    expect(cta).toHaveAttribute("href", `/practice/blocks/${BLOCK_ID}/review/3?filter=missed`);
    // Exactly one lime element on the page: the review CTA (the % glyph is not lime).
    const isLime = (el: Element) => [...el.classList].some((c) => /^(bg|text|fill|stroke|border)-lime$/.test(c));
    expect([...document.querySelectorAll("[class]")].filter(isLime)).toEqual([cta]);

    const cards = screen.getByRole("button", { name: t("results.make_cards") });
    expect(cards).toBeDisabled();
    expect(cards).toHaveAccessibleDescription(t("results.make_cards_unavailable"));
  });

  it("names every grid tile's status in words, not colour alone", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    const grid = await screen.findByRole("region", { name: t("results.grid_title") });
    const tiles = within(grid).getAllByRole("link");
    expect(tiles).toHaveLength(40);
    expect(within(grid).getByRole("link", { name: "Question 1, correct" })).toHaveAttribute("data-outcome", "correct");
    expect(within(grid).getByRole("link", { name: "Question 12, missed, flagged" })).toBeInTheDocument();
    expect(within(grid).getByRole("link", { name: "Question 7, missed, changed right → wrong" })).toBeInTheDocument();
    expect(within(grid).getByRole("link", { name: "Question 38, omitted" })).toBeInTheDocument();
    expect(within(grid).getByRole("link", { name: "Question 5, correct, flagged" })).toHaveAttribute(
      "href",
      `/practice/blocks/${BLOCK_ID}/review/5?filter=all`,
    );
    // A missed tile carries a visible glyph besides its colour.
    expect(within(grid).getByRole("link", { name: "Question 3, missed" }).querySelector("svg")).not.toBeNull();
    expect(questionLabel(t, QUESTIONS[8] as (typeof QUESTIONS)[number])).toBe(
      "Question 9, correct, changed wrong → right",
    );
  });

  it("marks weak topics with the word, draws the trend with a table, and shows the weights and insights", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    const topics = await screen.findByRole("region", { name: t("results.topics_title") });
    const cardio = within(topics).getByText("Cardiovascular").closest("li") as HTMLElement;
    expect(within(cardio).getByText(t("results.weak"))).toBeVisible();
    expect(within(topics).getByText("Renal").closest("li")).not.toHaveAttribute("data-weak");
    expect(within(topics).queryByRole("heading", { name: t("results.topics_competencies") })).toBeNull(); // empty group

    const trend = screen.getByRole("img", { name: t("results.trend_label", { count: 3, first: 54, last: 73 }) });
    expect(trend.querySelectorAll("circle")).toHaveLength(3);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText(`${t("results.percent", { percent: 73 })}`)).toBeInTheDocument();
    expect(screen.queryByText(/comfortable pass/i)).toBeNull();

    const weights = screen.getByRole("region", { name: t("results.weights_title") });
    const rows = within(weights).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-place", "above");
    expect(rows[0]).toHaveTextContent(t("results.weights_official", { min: 6, max: 10 }));
    expect(rows[2]).toHaveTextContent(t("results.weights_none"));

    const sees = screen.getByRole("region", { name: t("results.insights_kicker") });
    expect(sees).toHaveTextContent(
      t("results.insight.weakest_topic", { topic: "Cardiovascular", correct: 5, total: 10 }),
    );
    expect(sees).toHaveTextContent(t("results.insight.changes_hurt", { lost: 2, gained: 1 }));
    expect(sees.textContent).not.toMatch(/\{\w+\}/);
  });

  it("offers review of all questions when nothing was missed (still the one lime action)", async () => {
    const perfect = RESULTS.questions.map((q) => ({ ...q, outcome: "correct" as const }));
    hooks.results = { data: { ...RESULTS, questions: perfect, correct: 40, omitted: 0, percent: 100 }, error: null };
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    expect(await screen.findByRole("link", { name: t("results.review_all") })).toHaveAttribute(
      "href",
      `/practice/blocks/${BLOCK_ID}/review/1?filter=all`,
    );
    expect(screen.queryByRole("button", { name: t("results.make_cards") })).toBeNull();
    expect(screen.queryByText(t("results.stat_omitted"))).toBeNull();
  });

  it("shows a loading status while the results load", async () => {
    hooks.results = { data: undefined, error: null };
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    expect(await screen.findByRole("status")).toHaveTextContent(t("results.loading"));
  });

  it("a block still running links back to the exam view", async () => {
    hooks.results = { data: undefined, error: new ApiError("x", 409, "not_finished", "rid") };
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    expect(await screen.findByRole("heading", { name: t("results.not_finished_title") })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("results.back_to_exam") })).toHaveAttribute(
      "href",
      `/practice/blocks/${BLOCK_ID}/17`,
    );
  });

  it("a failure shows the error with a retry; a missing block has none", async () => {
    const refetch = vi.fn();
    hooks.results = {
      data: undefined,
      error: new ApiError("Boom", 500, "internal", "rid-1"),
      refetch,
      isFetching: false,
    };
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("rid-1");
    await userEvent.click(within(alert).getByRole("button", { name: t("results.retry") }));
    expect(refetch).toHaveBeenCalled();
  });

  it("a missing block has no retry", async () => {
    hooks.results = { data: undefined, error: new ApiError("Not found", 404, "not_found", null), refetch: vi.fn() };
    renderAt(`/practice/blocks/${BLOCK_ID}/results`);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(t("results.gone_body"));
    expect(within(alert).queryByRole("button")).toBeNull();
  });
});

describe("Review page", () => {
  it("shows the student's choice and the right answer in words, and the explanation", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/review/3?filter=missed`);
    const options = await screen.findByRole("list", { name: t("practice.options_label") });
    const items = within(options).getAllByRole("listitem");
    expect(items[0]).toHaveAttribute("data-state", "correct");
    expect(items[0]).toHaveTextContent(t("practice.correct_answer"));
    expect(items[2]).toHaveAttribute("data-state", "wrong");
    expect(items[2]).toHaveTextContent(`${t("practice.incorrect")} · ${t("practice.your_answer")}`);
    expect(screen.getByRole("region", { name: t("practice.explanation_label") })).toHaveTextContent("Why A for 3.");
    expect(screen.getByText(t("results.review_position", { index: 1, count: 11 }))).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: t("results.review_filter_label") });
    expect(within(nav).getByRole("link", { name: /Missed/ })).toHaveAttribute("aria-current", "page");
    // One lime action in the view: Next within the list (the correct option is marked in paper + word).
    const isLime = (el: Element) => [...el.classList].some((c) => /^(bg|text|fill|stroke|border)-lime$/.test(c));
    expect([...document.querySelectorAll("[class]")].filter(isLime)).toEqual([
      screen.getByRole("button", { name: t("results.review_next") }),
    ]);
  });

  it("moves within the filtered list with the buttons and the keys", async () => {
    const router = renderAt(`/practice/blocks/${BLOCK_ID}/review/3?filter=missed`);
    await userEvent.click(await screen.findByRole("button", { name: t("results.review_next") }));
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/7`);
    expect(router.state.location.search).toEqual({ filter: "missed" });
    await userEvent.keyboard("n");
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/8`);
    await userEvent.keyboard("{ArrowLeft}");
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/7`);
    await userEvent.keyboard("{ArrowRight}");
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/8`);
    expect(screen.getByRole("button", { name: t("results.review_prev") })).toBeEnabled();
  });

  it("does not take keys typed into a field", async () => {
    const router = renderAt(`/practice/blocks/${BLOCK_ID}/review/3?filter=missed`);
    await screen.findByRole("button", { name: t("results.review_next") });
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    await userEvent.keyboard("n{ArrowRight}");
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/3`);
    input.remove();
  });

  it("filter chips switch lists and the last question leads back to results", async () => {
    const router = renderAt(`/practice/blocks/${BLOCK_ID}/review/3?filter=missed`);
    const nav = await screen.findByRole("navigation", { name: t("results.review_filter_label") });
    await userEvent.click(within(nav).getByRole("link", { name: /Flagged/ }));
    expect(router.state.location.pathname).toBe(`/practice/blocks/${BLOCK_ID}/review/5`);
    expect(router.state.location.search).toEqual({ filter: "flagged" });
    act(() => router.history.push(`/practice/blocks/${BLOCK_ID}/review/33?filter=flagged`));
    expect(await screen.findByRole("link", { name: t("results.review_done") })).toHaveAttribute(
      "href",
      `/practice/blocks/${BLOCK_ID}/results`,
    );
    expect(screen.queryByRole("button", { name: t("results.review_next") })).toBeNull();
  });

  it("an empty list is not a link", async () => {
    hooks.results = { data: { ...RESULTS, questions: QUESTIONS.map((q) => ({ ...q, change: null })) }, error: null };
    renderAt(`/practice/blocks/${BLOCK_ID}/review/3?filter=all`);
    const nav = await screen.findByRole("navigation", { name: t("results.review_filter_label") });
    expect(within(nav).queryByRole("link", { name: /Changed/ })).toBeNull();
    expect(within(nav).getByText(t("results.filter_changed")).closest("[aria-disabled]")).not.toBeNull();
  });

  it("an omitted question says so, and a question outside the block is refused", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/review/38?filter=missed`);
    expect(await screen.findByText(t("results.review_unanswered"))).toBeInTheDocument();
  });

  it("refuses a number past the end of the block", async () => {
    renderAt(`/practice/blocks/${BLOCK_ID}/review/41?filter=all`);
    expect(await screen.findByRole("alert")).toHaveTextContent(t("results.review_missing"));
  });

  it("a block still running shows the not-finished state", async () => {
    hooks.results = { data: undefined, error: new ApiError("x", 409, "not_finished", null) };
    renderAt(`/practice/blocks/${BLOCK_ID}/review/1?filter=all`);
    expect(await screen.findByRole("heading", { name: t("results.not_finished_title") })).toBeInTheDocument();
  });
});

describe("Builder helpers (segment 09)", () => {
  it("the practice URL accepts a mock id only", () => {
    expect(parsePracticeSearch({ mock: "mock1" })).toEqual({ exam: undefined, mock: "mock1" });
    expect(parsePracticeSearch({ mock: "mock3", exam: "step2ck" })).toEqual({ exam: "step2ck", mock: undefined });
    expect(parsePracticeSearch({ mock: "<x>" }).mock).toBeUndefined();
  });

  it("block chips stop at four, the exam's rule, and what the pool can fill", () => {
    const rules = { exams: { step1: { blocks: 7 }, short: { blocks: 2 } } } as unknown as Parameters<
      typeof maxSections
    >[0];
    expect(maxSections(rules, "step1")).toBe(4);
    expect(maxSections(rules, "short")).toBe(2);
    expect(maxSections(undefined, "step1")).toBe(4);
    expect(sectionsThatFit(100, 40, 4)).toBe(2);
    expect(sectionsThatFit(10, 40, 4)).toBe(1);
    expect(sectionsThatFit(400, 40, 3)).toBe(3);
  });

  it("only the contract's mock ids are offered", () => {
    const m = (id: string) => ({ id, title: id, blocks: 2, questions: 80 });
    expect(startableMocks([m("mock1"), m("mock9"), m("mock2")]).map((x) => x.id)).toEqual(["mock1", "mock2"]);
  });
});

describe("results copy", () => {
  const dir = import.meta.dirname;
  const files = readdirSync(dir).filter((f) => /\.tsx?$/.test(f) && !f.includes(".test.") && f !== "fixtures.ts");
  const source = files.map((f) => readFileSync(path.join(dir, f), "utf8")).join("\n");

  it("every results.* string the screens read is a required ui_copy key", () => {
    const used = new Set([...source.matchAll(/\bt\(\s*"(results\.[a-z_.]+)"/g)].map((m) => m[1] as string));
    for (const f of ["all", "missed", "flagged", "changed"]) used.add(`results.filter_${f}`);
    for (const c of ["right_to_wrong", "wrong_to_right", "wrong_to_wrong"]) used.add(`results.change.${c}`);
    for (const p of ["below", "within", "above"]) used.add(`results.weights_${p}`);
    used.add("results.kicker");
    used.add("results.kicker_mock");
    expect(used.size).toBeGreaterThan(80);
    const required = new Set(REQUIRED_UI_COPY_KEYS);
    expect([...used].filter((k) => !required.has(k))).toEqual([]);
  });
});
