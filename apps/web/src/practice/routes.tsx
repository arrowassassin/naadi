import { ErrorState } from "@naadi/ui";
import { type AnyRoute, createRoute, Navigate, Outlet } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { ApiError } from "../auth/apiError";
import { ExamPage } from "../exam/ExamPage";
import { ResultsPage } from "../exam/results/ResultsPage";
import { parseReviewSearch, ReviewPage } from "../exam/results/ReviewPage";
import { useBlock, useCreateSingleBlock } from "./api";
import { BuilderPage } from "./BuilderPage";
import { useCopy } from "./copy";
import { QuestionPage } from "./QuestionPage";

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EXAM = /^[a-z0-9]{2,16}$/;

const MOCK = /^mock[12]$/;

export interface PracticeSearch {
  exam?: string;
  /** From the Courses page's Mock exams group: preselect its exam and bring the Mock exams group into view. */
  mock?: "mock1" | "mock2";
}

export function parsePracticeSearch(s: Record<string, unknown>): PracticeSearch {
  return {
    exam: typeof s.exam === "string" && EXAM.test(s.exam) ? s.exam : undefined,
    mock: typeof s.mock === "string" && MOCK.test(s.mock) ? (s.mock as "mock1" | "mock2") : undefined,
  };
}

const parseBlockParams = (p: Record<string, string>) => {
  if (!UUID.test(p.blockId ?? "")) throw new Error("invalid block");
  return { blockId: p.blockId as string };
};

/**
 * `/practice/question/$questionId`: where a search hit for a question lands. It opens a one-question tutor
 * block (built by the server) so the stem and options show and the answer appears only after the student
 * submits.
 */
function SingleQuestion({ questionId }: { questionId: string }) {
  const t = useCopy();
  const open = useCreateSingleBlock();
  const started = useRef(false);
  const { mutate } = open;
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    mutate(questionId);
  }, [mutate, questionId]);
  if (open.data) {
    return <Navigate to="/practice/blocks/$blockId/$n" params={{ blockId: open.data.id, n: 1 }} replace />;
  }
  if (open.error) {
    return (
      <section className="flex flex-col gap-6 pt-8">
        <ErrorState
          title={t("practice.question_gone_title")}
          message={open.error.message}
          requestId={open.error instanceof ApiError ? open.error.requestId : null}
          live
        />
      </section>
    );
  }
  return (
    <p role="status" className="m-0 pt-10 text-text-2">
      {t("practice.building")}
    </p>
  );
}

/**
 * A block's question route: timed and exam blocks run on the exam view (segment 09, server clock), tutor
 * blocks on the question view. Without the block (offline), the question view decides from its cached copy.
 */
function BlockQuestion({ blockId, n }: { blockId: string; n: number }) {
  const t = useCopy();
  const block = useBlock(blockId);
  if (block.isPending && !block.data) {
    return (
      <p role="status" className="m-0 pt-10 text-text-2">
        {t("exam.loading")}
      </p>
    );
  }
  if (block.data && block.data.config.mode !== "tutor") return <ExamPage blockId={blockId} n={n} />;
  return <QuestionPage blockId={blockId} n={n} />;
}

/** One route tree under the protected app layout: `router.tsx` adds `practiceRoutes(appRoute)`. */
export function practiceRoutes(parent: AnyRoute) {
  const practice = createRoute({ getParentRoute: () => parent, path: "/practice", component: Outlet });
  const builder = createRoute({
    getParentRoute: () => practice,
    path: "/",
    validateSearch: parsePracticeSearch,
    component: function BuilderRoute() {
      const { exam, mock } = builder.useSearch();
      return <BuilderPage key={exam ?? ""} exam={exam} mock={mock} />;
    },
  });
  const question = createRoute({
    getParentRoute: () => practice,
    path: "blocks/$blockId/$n",
    params: {
      parse: (p: Record<string, string>) => {
        const n = Number(p.n);
        if (!UUID.test(p.blockId ?? "") || !Number.isInteger(n) || n < 1 || n > 200) throw new Error("invalid block");
        return { blockId: p.blockId as string, n };
      },
      stringify: (p: { blockId: string; n: number }) => ({ blockId: p.blockId, n: String(p.n) }),
    },
    component: function QuestionRoute() {
      const { blockId, n } = question.useParams();
      return <BlockQuestion blockId={blockId} n={n} />;
    },
  });
  const single = createRoute({
    getParentRoute: () => practice,
    path: "question/$questionId",
    params: {
      parse: (p: Record<string, string>) => {
        if (!ID.test(p.questionId ?? "")) throw new Error("invalid question id");
        return { questionId: p.questionId as string };
      },
      stringify: (p: { questionId: string }) => p,
    },
    component: function SingleRoute() {
      return <SingleQuestion questionId={single.useParams().questionId} />;
    },
  });
  // Results and review of a finished timed/exam block (segment 09). Static segments outrank `$n`.
  const results = createRoute({
    getParentRoute: () => practice,
    path: "blocks/$blockId/results",
    params: { parse: parseBlockParams, stringify: (p: { blockId: string }) => p },
    component: function ResultsRoute() {
      return <ResultsPage blockId={results.useParams().blockId} />;
    },
  });
  const review = createRoute({
    getParentRoute: () => practice,
    path: "blocks/$blockId/review/$n",
    validateSearch: parseReviewSearch,
    params: {
      parse: (p: Record<string, string>) => {
        const n = Number(p.n);
        if (!Number.isInteger(n) || n < 1 || n > 1000) throw new Error("invalid question");
        return { ...parseBlockParams(p), n };
      },
      stringify: (p: { blockId: string; n: number }) => ({ blockId: p.blockId, n: String(p.n) }),
    },
    component: function ReviewRoute() {
      const { blockId, n } = review.useParams();
      const { filter } = review.useSearch();
      return <ReviewPage key={blockId} blockId={blockId} n={n} filter={filter} />;
    },
  });
  return practice.addChildren([builder, question, results, review, single]);
}
