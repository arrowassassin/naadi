import { type ReviewItemDetail, UI_COPY_DEFAULTS, uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../auth/apiError";
import { approveBlockedKey, awaitingMine, contradictsApproval, decisionBlockedKey } from "./logic";
import { FLAW_CODES, findingVars, LINT_RULE_IDS, lintKey, PSYCH_FLAGS } from "./rules";

const hooks = vi.hoisted(() => ({
  items: {} as Record<string, unknown>,
  summary: {} as Record<string, unknown>,
  item: {} as Record<string, unknown>,
  submit: { mutate: vi.fn(), isPending: false, error: null as unknown, reset: vi.fn() },
  navigate: vi.fn(),
  act: vi.fn(),
}));

vi.mock("../copy/useUiText", () => ({
  useUiText: () => (key: string, vars?: Record<string, string>) => uiText(null, key, vars),
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, ...rest }: { children: ReactNode; to: string; params?: Record<string, string> }) => (
    <a href={to.replace("$itemId", params?.itemId ?? "")} {...rest}>
      {children}
    </a>
  ),
  useNavigate: () => hooks.navigate,
}));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => true }));
vi.mock("./DraftPanel", () => ({ DraftPanel: () => <div data-testid="draft-panel-stub" /> }));
vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useReviewItems: () => hooks.items,
  useReviewSummary: () => hooks.summary,
  useReviewItem: () => hooks.item,
  useSubmitReview: () => hooks.submit,
  useResubmit: () => ({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() }),
  useRunChecks: () => ({ mutate: vi.fn(), isPending: false, error: null, variables: undefined }),
  useRecompute: () => ({ mutate: vi.fn(), isPending: false, error: null, data: undefined }),
  usePsychAction: () => ({ mutate: hooks.act, isPending: false, error: null, isSuccess: false }),
  useActMinN: () => ({ data: 100 }),
}));
vi.mock("../auth/session", () => ({ useAuth: () => ({ status: "signed-in", session: { user: { id: "u1" } } }) }));

import { FindingList, SolverBlock } from "./Findings";
import { GatesPanel } from "./Gates";
import { ItemPreview } from "./ItemPreview";
import { PsychometricsPanel } from "./Psychometrics";
import { RatingForm } from "./RatingForm";
import { ReviewHub } from "./ReviewHub";
import { ReviewItemPage } from "./ReviewItemPage";
import { ReviewsPanel } from "./ReviewsPanel";

function detail(over: Partial<ReviewItemDetail> = {}): ReviewItemDetail {
  return {
    id: "ai-abc",
    exam: "step1",
    status: "in_review",
    origin: "ai",
    difficulty: "medium",
    stem_html: "<p>A 54-year-old man has a sodium of 118. Which is the most likely cause?</p>",
    options: ["A", "B", "C", "D", "E"].map((l) => ({ label: l, html: `<p>Option ${l}</p>` })),
    answer: "C",
    answer_html: null,
    explanation_html: "<p>Because of ADH.</p>",
    why_not_html: "Why the others are wrong: A — no. B — no. D — no. E — no.",
    educational_objective: "Recognise SIADH.",
    taxonomy: [{ id: "t1", kind: "topic", label: "Sodium" }],
    source_id: "naadi-original-v1",
    created_at: "2026-11-17T00:00:00Z",
    author: { model: "m/author", prompt_hash: "abc123", run_id: "run-1", excerpt_ids: ["ex-1"], at: null },
    lint: [],
    solver: {
      agrees: true,
      choice: "C",
      confidence: 0.9,
      defensibility: { A: 0.1, B: 0.2, C: 0.95, D: 0.3, E: 0.05 },
      cover_matches: true,
      model: "m/solver",
      independent: true,
      error: null,
    },
    solver_findings: [],
    gates: { lint_ok: true, source_ok: true, cross_check_ok: true, independent: true, blockers: [] },
    resolution: { state: "awaiting", needed: 1, submitted: 0, round: 1 },
    reviews: [],
    hidden_reviews: 0,
    can_review: { allowed: true, reason: null },
    stats: null,
    required_approvals: 1,
    ...over,
  } as ReviewItemDetail;
}

const queueRow = (over: Record<string, unknown> = {}) => ({
  id: "ai-1",
  exam: "step1",
  status: "in_review",
  origin: "ai",
  difficulty: "hard",
  stem_preview: "A woman presents with fatigue",
  errors: 1,
  warnings: 2,
  state: "awaiting",
  submitted: 0,
  needed: 1,
  mine_submitted: false,
  psych_flags: [],
  ...over,
});

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  hooks.submit.mutate.mockReset();
  hooks.navigate.mockReset();
  hooks.act.mockReset();
  hooks.submit.error = null;
  hooks.submit.isPending = false;
});

describe("copy coverage", () => {
  it("has a plain-words sentence for every lint rule S01-S05, L01-L18 and B01-B04", () => {
    const expected = [
      ...["S01", "S02", "S03", "S04", "S05"],
      ...Array.from({ length: 18 }, (_, i) => `L${String(i + 1).padStart(2, "0")}`),
      ...["B01", "B02", "B03", "B04"],
    ];
    expect([...LINT_RULE_IDS]).toEqual(expected);
    const missing = expected.filter((id) => !UI_COPY_DEFAULTS[`review.lint.${id.toLowerCase()}`]);
    expect(missing).toEqual([]);
    for (const id of expected) expect(lintKey(id)).toBe(`review.lint.${id.toLowerCase()}`);
  });

  it("words all 13 flaws, the psychometric flags, gate blockers and error codes", () => {
    expect(FLAW_CODES).toHaveLength(13);
    for (const f of FLAW_CODES) expect(UI_COPY_DEFAULTS[`review.flaw.${f}`], f).toBeTruthy();
    for (const f of PSYCH_FLAGS) expect(UI_COPY_DEFAULTS[`review.psych_flag.${f}`], f).toBeTruthy();
    for (const b of ["lint_errors", "source_check_failed", "model_cross_check_failed", "review_not_independent"]) {
      expect(UI_COPY_DEFAULTS[`review.blocked.${b}`], b).toBeTruthy();
    }
  });

  it("fills a rule's sentence with the offending words", () => {
    const t = (k: string, v: Record<string, string>) => uiText(null, k, v);
    const f = { rule: "L02", severity: "error" as const, target: "stem_html", params: { words: ["except", "not"] } };
    expect(t(lintKey("L02"), findingVars(f))).toBe(
      'The lead-in is negative ("except, not"). Ask for the one best answer instead of the exception.',
    );
  });
});

describe("approval rules", () => {
  const ok = {
    accuracy: "correct",
    single_best_answer: true,
    cognitive_level: "application",
    difficulty: "medium",
    taxonomy_ok: true,
  } as const;
  it("explains each reason approve is unavailable", () => {
    expect(approveBlockedKey(detail(), ok)).toBeNull();
    expect(approveBlockedKey(detail(), {})).toBe("review.approve_blocked_ratings");
    expect(approveBlockedKey(detail(), { ...ok, accuracy: "incorrect" })).toBe("review.approve_blocked_contradiction");
    expect(approveBlockedKey(detail(), { ...ok, single_best_answer: false })).toBe(
      "review.approve_blocked_contradiction",
    );
    expect(approveBlockedKey(detail(), { ...ok, taxonomy_ok: false })).toBe("review.approve_blocked_contradiction");
    expect(approveBlockedKey(detail({ gates: { ...detail().gates, blockers: ["lint_errors"] } }), ok)).toBe(
      "review.approve_blocked_gates",
    );
    expect(approveBlockedKey(detail({ can_review: { allowed: false, reason: "round_closed" } }), ok)).toBe(
      "review.cannot.round_closed",
    );
    expect(contradictsApproval({ accuracy: "minor_issue" })).toBe(false);
  });
  it("lets revise and reject through when only ratings are missing", () => {
    expect(decisionBlockedKey(detail(), {})).toBe("review.decide_blocked_ratings");
    expect(decisionBlockedKey(detail(), { ...ok, accuracy: "incorrect" })).toBeNull();
  });
  it("picks rows waiting on you", () => {
    expect(awaitingMine({ state: "awaiting", mine_submitted: false })).toBe(true);
    expect(awaitingMine({ state: "awaiting", mine_submitted: true })).toBe(false);
    expect(awaitingMine({ state: "approved", mine_submitted: false })).toBe(false);
  });
});

async function fillRatings(
  user: ReturnType<typeof userEvent.setup>,
  over: { accuracy?: string; single?: string } = {},
) {
  await user.click(screen.getByRole("radio", { name: over.accuracy ?? "Correct" }));
  const single = screen.getByRole("group", { name: "Is there exactly one best answer?" });
  await user.click(within(single).getByRole("radio", { name: over.single ?? "Yes" }));
  await user.click(screen.getByRole("radio", { name: "Application" }));
  await user.click(screen.getByRole("radio", { name: "Medium" }));
  const tax = screen.getByRole("group", { name: /Are the taxonomy tags right/ });
  await user.click(within(tax).getByRole("radio", { name: "Yes" }));
}

describe("RatingForm", () => {
  it("keeps every decision off, with the reason in words, until every rating is set", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    wrap(<RatingForm item={detail()} pending={false} error={null} onSubmit={onSubmit} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("review-why")).toHaveTextContent("rate every part of the form first");
    await fillRatings(user);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    expect(approve).toHaveAttribute("data-variant", "primary");
    await user.type(screen.getByLabelText("Note (optional)"), "Looks right");
    await user.click(screen.getByRole("checkbox", { name: /Ask for a second review/ }));
    await user.click(screen.getByRole("checkbox", { name: "Negative lead-in" }));
    await user.click(approve);
    expect(onSubmit).toHaveBeenCalledWith({
      decision: "approve",
      ratings: {
        accuracy: "correct",
        single_best_answer: true,
        cognitive_level: "application",
        difficulty: "medium",
        taxonomy_ok: true,
        flaws: ["negative_lead_in"],
      },
      note: "Looks right",
      request_second: true,
    });
  });

  it("switches approve off when the ratings contradict it, but allows reject", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    wrap(<RatingForm item={detail()} pending={false} error={null} onSubmit={onSubmit} />);
    await fillRatings(user, { accuracy: "Incorrect" });
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("review-why")).toHaveTextContent("say the item is incorrect");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ decision: "reject", request_second: false }));
  });

  it("explains a gate blocker and a reviewer who may not review", async () => {
    const user = userEvent.setup();
    const blocked = detail({
      gates: { ...detail().gates, cross_check_ok: false, blockers: ["model_cross_check_failed"] },
    });
    const { unmount } = wrap(<RatingForm item={blocked} pending={false} error={null} onSubmit={vi.fn()} />);
    await fillRatings(user);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByTestId("review-why")).toHaveTextContent("a publish gate has not passed");
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    unmount();
    wrap(
      <RatingForm
        item={detail({ can_review: { allowed: false, reason: "already_reviewed_by_you" } })}
        pending={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByTestId("review-why")).toHaveTextContent("You have already reviewed this question");
  });

  it("hides the second-review box when the config requires two approvals", () => {
    wrap(<RatingForm item={detail({ required_approvals: 2 })} pending={false} error={null} onSubmit={vi.fn()} />);
    expect(screen.queryByRole("checkbox", { name: /Ask for a second review/ })).toBeNull();
    expect(screen.getByText(/Two different admins must review/)).toBeVisible();
  });

  it("words a server error with its request id", () => {
    wrap(
      <RatingForm
        item={detail()}
        pending={false}
        error={new ApiError("x", 422, "approve_contradicts_ratings", "req-9")}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("cannot be approved");
    expect(screen.getByRole("alert")).toHaveTextContent("req-9");
  });
});

describe("ReviewsPanel", () => {
  it("explains blindness without showing the other review", () => {
    wrap(
      <ReviewsPanel
        item={detail({ hidden_reviews: 1, resolution: { state: "awaiting", needed: 2, submitted: 1, round: 1 } })}
      />,
    );
    expect(screen.getByTestId("review-hidden")).toHaveTextContent(
      "A second review has been submitted. You'll see it once you have submitted yours.",
    );
    expect(screen.queryByTestId("review-entry")).toBeNull();
  });

  it("shows the adjudication banner to an admin who may decide, and a waiting note to a reviewer", () => {
    const { unmount } = wrap(
      <ReviewsPanel item={detail({ resolution: { state: "adjudication", needed: 2, submitted: 2, round: 1 } })} />,
    );
    expect(screen.getByTestId("review-adjudication")).toHaveTextContent("Your decision is final");
    unmount();
    wrap(
      <ReviewsPanel
        item={detail({
          resolution: { state: "adjudication", needed: 2, submitted: 2, round: 1 },
          can_review: { allowed: false, reason: "already_reviewed_by_you" },
        })}
      />,
    );
    expect(screen.getByTestId("review-adjudication")).toHaveTextContent("third admin has to decide");
  });

  it("lists past reviews with decision words and ratings in words", () => {
    wrap(
      <ReviewsPanel
        item={detail({
          reviews: [
            {
              id: 1,
              round: 1,
              decision: "revise",
              adjudication: false,
              request_second: false,
              is_mine: true,
              ratings: {
                accuracy: "minor_issue",
                single_best_answer: true,
                cognitive_level: "recall",
                flaws: ["hinged"],
                difficulty: "easy",
                taxonomy_ok: true,
              },
              note: "Fix option D",
              created_at: "2026-11-17T00:00:00Z",
            },
          ],
        })}
      />,
    );
    const entry = screen.getByTestId("review-entry");
    expect(entry).toHaveTextContent("Sent back");
    expect(entry).toHaveTextContent("Minor issue");
    expect(entry).toHaveTextContent("Depends on another question");
    expect(screen.getByTestId("review-note")).toHaveTextContent("Fix option D");
  });
});

describe("findings, solver and gates", () => {
  it("groups lint findings by severity with the rule id and a sentence", () => {
    wrap(
      <FindingList
        findings={[
          { rule: "L03", severity: "warn", target: "stem_html", params: { words: ["always"] } },
          { rule: "L01", severity: "error", target: "options", params: { options: ["A"] } },
          { rule: "L18", severity: "info", target: "difficulty", params: { confidence: 0.9, requested: "hard" } },
        ]}
      />,
    );
    const groups = screen.getAllByRole("region");
    expect(groups.map((g) => g.getAttribute("data-severity"))).toEqual(["error", "warn", "info"]);
    expect(screen.getByText("L03")).toBeVisible();
    expect(screen.getByText(/absolute or vague-frequency terms \(always\)/)).toBeVisible();
  });

  it("states the solver's result in words and numbers, independence as yes or no", () => {
    wrap(<SolverBlock item={detail()} />);
    const s = screen.getByTestId("review-solver");
    expect(s).toHaveTextContent("C, which matches the key (C)");
    expect(s).toHaveTextContent("90%");
    expect(s).toHaveTextContent("Yes");
    expect(screen.getByText("95%")).toBeVisible();
    expect(screen.getAllByText("Defensible").length).toBeGreaterThan(0);
  });

  it("says a solver that disagrees disagrees, and one that never ran", () => {
    const { unmount } = wrap(
      <SolverBlock
        item={detail({
          solver: {
            ...(detail().solver as NonNullable<ReviewItemDetail["solver"]>),
            agrees: false,
            choice: "B",
            independent: false,
          },
        })}
      />,
    );
    expect(screen.getByTestId("review-solver")).toHaveTextContent("B, but the key is C");
    expect(screen.getByTestId("review-solver")).toHaveTextContent("No");
    unmount();
    wrap(<SolverBlock item={detail({ solver: null })} />);
    expect(screen.getByTestId("review-solver-none")).toBeVisible();
  });

  it("lists the four gates passed or failed in words with what to do", () => {
    wrap(
      <GatesPanel
        item={detail({
          gates: {
            lint_ok: false,
            source_ok: true,
            cross_check_ok: false,
            independent: true,
            blockers: ["lint_errors", "model_cross_check_failed"],
          },
        })}
        canRun
      />,
    );
    const gates = screen.getByTestId("review-gates");
    expect(within(gates).getAllByText("Failed")).toHaveLength(2);
    expect(within(gates).getAllByText("Passed")).toHaveLength(2);
    expect(gates).toHaveTextContent("Run the blind solver");
    expect(screen.getByRole("button", { name: "Run checks" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Re-run blind solver" })).toBeEnabled();
    expect(screen.getByTestId("review-blocked")).toBeVisible();
  });
});

describe("ItemPreview", () => {
  it("shows the item as a student sees it with the key and explanations labelled", () => {
    wrap(<ItemPreview item={detail()} />);
    const list = screen.getByRole("list", { name: "Answer options" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(within(list).getByText("Key")).toBeVisible();
    expect(screen.getByText("Admin view: key shown")).toBeVisible();
    expect(screen.getByText("Why it's C")).toBeVisible();
    expect(screen.getByText("Recognise SIADH.")).toBeVisible();
  });
});

describe("PsychometricsPanel", () => {
  const stats = {
    n: 40,
    p_correct: 0.12,
    point_biserial: 0.02,
    option_counts: { A: 5, B: 20, C: 5, D: 5, E: 5 },
    median_seconds: 95,
    report_count: 2,
    computed_at: null,
    flags: ["p_too_low", "reports"],
    can_act: false,
  };
  it("shows the numbers, a bar plus count per option and the flags in words", () => {
    wrap(<PsychometricsPanel item={detail({ status: "published", stats })} />);
    expect(screen.getByTestId("psych-n")).toHaveTextContent("40");
    expect(screen.getByTestId("psych-options")).toHaveTextContent("20 · 50%");
    expect(screen.getByTestId("psych-flags")).toHaveTextContent("Too hard (few answer correctly)");
    expect(screen.getByTestId("psych-flags")).toHaveTextContent("Reported by students");
  });
  it("disables Retire with the reason until enough responses exist", async () => {
    const user = userEvent.setup();
    wrap(<PsychometricsPanel item={detail({ status: "published", stats })} />);
    expect(screen.getByRole("button", { name: "Retire" })).toBeDisabled();
    expect(screen.getByTestId("psych-needed")).toHaveTextContent(
      "Responses needed before this item can be retired: 100 (so far 40).",
    );
    expect(screen.getByTestId("psych-retire-reason")).toHaveTextContent("until there are 100 responses");
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(hooks.act).toHaveBeenCalledWith("keep");
  });
  it("enables Retire once the item can be acted on", async () => {
    const user = userEvent.setup();
    wrap(<PsychometricsPanel item={detail({ status: "published", stats: { ...stats, n: 150, can_act: true } })} />);
    await user.click(screen.getByRole("button", { name: "Retire" }));
    expect(hooks.act).toHaveBeenCalledWith("retire");
  });
  it("tells the admin when nothing is flagged", () => {
    wrap(<PsychometricsPanel item={detail({ status: "published", stats: { ...stats, flags: [], can_act: true } })} />);
    expect(screen.getByRole("button", { name: "Retire" })).toBeDisabled();
    expect(screen.getByTestId("psych-retire-reason")).toHaveTextContent("carry a flag");
  });
});

describe("ReviewHub queue", () => {
  const summary = {
    ai_drafts: 2,
    seed_spot_check: 685,
    psychometric_flags: 0,
    adjudication: 1,
    needs_check: 3,
    required_approvals: 1,
  };
  it("renders rows with origin, state and lint counts as words and numbers, and filter counts", async () => {
    const user = userEvent.setup();
    hooks.summary = { data: summary, isError: false };
    hooks.items = {
      isPending: false,
      isError: false,
      data: {
        pages: [
          { items: [queueRow(), queueRow({ id: "seed-1", origin: "seed", mine_submitted: true, state: "awaiting" })] },
        ],
      },
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    };
    const onFilter = vi.fn();
    wrap(<ReviewHub filter="ai_drafts" onFilter={onFilter} />);
    expect(screen.getByTestId("count-seed_spot_check")).toHaveTextContent("685");
    const rows = screen.getAllByTestId("queue-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("AI-drafted");
    expect(rows[0]).toHaveTextContent("Awaiting review");
    expect(rows[0]).toHaveTextContent("Lint errors: 1");
    expect(rows[0]).toHaveTextContent("Lint warnings: 2");
    expect(rows[1]).toHaveTextContent("You reviewed this");
    expect(screen.getByRole("button", { name: "Load more" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Seed spot-check/ }));
    expect(onFilter).toHaveBeenCalledWith("seed_spot_check");
    expect(screen.getByRole("button", { name: /AI drafts/ })).toHaveAttribute("aria-current", "true");
  });

  it("makes Review next the one lime button and opens the first item awaiting you", async () => {
    const user = userEvent.setup();
    hooks.summary = { data: summary, isError: false };
    hooks.items = {
      isPending: false,
      isError: false,
      data: { pages: [{ items: [queueRow({ id: "done", mine_submitted: true }), queueRow({ id: "todo" })] }] },
      hasNextPage: false,
    };
    wrap(<ReviewHub filter="ai_drafts" onFilter={vi.fn()} />);
    const next = screen.getByRole("button", { name: "Review next" });
    expect(next).toHaveAttribute("data-variant", "primary");
    expect(document.querySelectorAll('[data-variant="primary"]')).toHaveLength(1);
    await user.click(next);
    expect(hooks.navigate).toHaveBeenCalledWith({ to: "/admin/review/$itemId", params: { itemId: "todo" } });
  });

  it("disables Review next with its reason on an empty list, and shows the empty copy", () => {
    hooks.summary = { data: summary, isError: false };
    hooks.items = { isPending: false, isError: false, data: { pages: [{ items: [] }] }, hasNextPage: false };
    wrap(<ReviewHub filter="seed_spot_check" onFilter={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Review next" })).toBeDisabled();
    expect(screen.getByText("Nothing in this list is waiting for your review.")).toBeVisible();
    expect(screen.getByTestId("queue-empty")).toHaveTextContent("Every seed question has been reviewed");
  });

  it("shows loading skeletons and an error state with retry and request id", async () => {
    const user = userEvent.setup();
    hooks.summary = { data: undefined, isError: false };
    hooks.items = { isPending: true, isError: false, data: undefined };
    const { unmount } = wrap(<ReviewHub filter="ai_drafts" onFilter={vi.fn()} />);
    expect(screen.getByTestId("queue-loading")).toHaveAttribute("aria-busy", "true");
    unmount();
    const refetch = vi.fn();
    hooks.items = {
      isPending: false,
      isError: true,
      data: undefined,
      error: new ApiError("x", 500, "internal", "req-1"),
      refetch,
      isFetching: false,
    };
    wrap(<ReviewHub filter="ai_drafts" onFilter={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Request ID req-1");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalled();
  });
});

describe("ReviewItemPage outcomes", () => {
  const cases: [string, string][] = [
    ["recorded", "Review saved"],
    ["published", "Published"],
    ["revise", "Sent back for revision"],
    ["rejected", "Rejected"],
    ["adjudication", "Needs a deciding review"],
  ];
  for (const [outcome, title] of cases) {
    it(`shows "${title}" for the ${outcome} outcome and the next-item button`, async () => {
      const user = userEvent.setup();
      hooks.item = { isPending: false, data: detail(), error: null };
      hooks.submit.mutate = vi.fn((_body, opts: { onSuccess: (r: { outcome: string }) => void }) =>
        opts.onSuccess({ outcome }),
      );
      wrap(<ReviewItemPage itemId="ai-abc" />);
      await fillRatings(user);
      await user.click(screen.getByRole("button", { name: "Approve" }));
      const panel = await screen.findByTestId("review-outcome");
      expect(panel).toHaveAttribute("data-outcome", outcome);
      expect(within(panel).getByRole("heading", { name: title })).toBeVisible();
      expect(within(panel).getByRole("button", { name: "Review the next question" })).toHaveAttribute(
        "data-variant",
        "primary",
      );
      expect(screen.queryByTestId("review-form")).toBeNull();
    });
  }

  it("offers Resubmit, not the rating form, for a draft", () => {
    hooks.item = {
      isPending: false,
      data: detail({ status: "draft", can_review: { allowed: false, reason: "status_not_reviewable" } }),
      error: null,
    };
    wrap(<ReviewItemPage itemId="ai-abc" />);
    expect(screen.getByRole("button", { name: "Resubmit for review" })).toHaveAttribute("data-variant", "primary");
    expect(screen.queryByTestId("review-form")).toBeNull();
  });

  it("shows an error state with retry for a question that cannot be loaded", () => {
    hooks.item = {
      isPending: false,
      data: undefined,
      error: new ApiError("x", 503, "authoring_unavailable", "r-2"),
      refetch: vi.fn(),
      isFetching: false,
    };
    wrap(<ReviewItemPage itemId="ai-abc" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Question authoring is not available right now.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  it("shows the seed spot-check form for a published seed question that is not yet human-reviewed", () => {
    hooks.item = { isPending: false, data: detail({ origin: "seed", status: "published" }), error: null };
    wrap(<ReviewItemPage itemId="seed-1" />);
    expect(screen.getByTestId("review-form")).toBeVisible();
    expect(screen.getByText("Seed question")).toBeVisible();
  });
});
