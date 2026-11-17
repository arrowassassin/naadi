import type { ActiveExam } from "@naadi/shared";
import { Link } from "@tanstack/react-router";
import { Timer } from "lucide-react";
import { useCopy } from "../practice/copy";

/** The exam's own screens (its block route, Results after the finish): never gated. */
export function onExamRoute(pathname: string, active: ActiveExam): boolean {
  return pathname.startsWith(`/practice/blocks/${active.block_id}`);
}

/**
 * While a timed/exam session runs, Search and practice (tutor blocks, the Builder, single questions) are closed
 * everywhere but the exam itself: the server refuses them too (409 exam_active), this keeps the UI honest.
 */
export function examGates(pathname: string, active: ActiveExam | null | undefined): boolean {
  if (!active || onExamRoute(pathname, active)) return false;
  return pathname === "/search" || pathname.startsWith("/search/") || pathname.startsWith("/practice");
}

function ReturnLink({ active, className }: { active: ActiveExam; className?: string }) {
  const t = useCopy();
  return (
    <Link to="/practice/blocks/$blockId/$n" params={{ blockId: active.block_id, n: 1 }} className={className}>
      {t("exam.active.return")}
    </Link>
  );
}

/** A slim notice on every other page: the exam is still running on the server's clock. */
export function ExamActiveBanner({ active }: { active: ActiveExam }) {
  const t = useCopy();
  return (
    <div
      role="status"
      data-exam-active-banner=""
      className="mx-auto mb-4 flex w-full max-w-(--content-max) flex-wrap items-center gap-3 rounded-block border border-line-control bg-surface-1 px-4 py-3"
    >
      <Timer aria-hidden="true" size={18} className="shrink-0 text-text-2" />
      <span className="min-w-0 flex-1 text-text-2">
        {t(active.mode === "exam" ? "exam.active.banner_exam" : "exam.active.banner_timed")}
      </span>
      <ReturnLink
        active={active}
        className="inline-flex min-h-11 items-center rounded-pill border border-line-control px-4 font-semibold text-text no-underline hover:bg-surface-2 hover:text-text"
      />
    </div>
  );
}

/** What Search and practice show while a session runs. One lime CTA: back to the exam. */
export function ExamActiveGate({ active }: { active: ActiveExam }) {
  const t = useCopy();
  return (
    <section aria-labelledby="exam-gate-h" className="flex max-w-[40rem] flex-col gap-5 py-10">
      <h1 id="exam-gate-h" className="m-0 font-bold font-display text-headline">
        {t("exam.active.gate_title")}
      </h1>
      <p className="m-0 text-text-2">{t("exam.active.gate_body")}</p>
      <div>
        <ReturnLink
          active={active}
          className="inline-flex min-h-14 items-center justify-center rounded-pill bg-lime px-7 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
        />
      </div>
    </section>
  );
}
