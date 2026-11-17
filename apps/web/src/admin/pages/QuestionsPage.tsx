import { cx } from "@naadi/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { api, EXAMS, QUESTION_STATUSES, qk } from "../api";
import {
  AdminPage,
  Empty,
  Failure,
  FilterPills,
  fmtDate,
  INPUT,
  Loading,
  MoreButton,
  STATUS_KEYS,
  StatusChip,
  useT,
} from "../components/ui";
import type { QuestionsSearch } from "../routes";
import { EXAM_KEYS } from "./ChaptersPage";

export const DIFFICULTY_KEYS: Record<string, string> = {
  easy: "admin.questions.difficulty.easy",
  medium: "admin.questions.difficulty.medium",
  hard: "admin.questions.difficulty.hard",
};

const CTA =
  "inline-flex min-h-14 items-center gap-2 rounded-pill bg-lime px-7 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink";

/** `/admin/questions`: every question with status and difficulty (admin-only: never a student route). */
export function QuestionsPage({ search }: { search: QuestionsSearch }) {
  const t = useT();
  const navigate = useNavigate();
  const exam = search.exam ?? "step1";
  const status = search.status ?? "all";
  const [q, setQ] = useState(search.q ?? "");
  const params = {
    exam,
    status: status === "all" ? undefined : status,
    q: search.q,
    chapter_id: search.chapter,
    limit: 40,
  };
  const list = useInfiniteQuery({
    queryKey: qk.questions(params),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.questions({ ...params, cursor: pageParam }, signal),
    getNextPageParam: (p) => p.next_cursor,
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const set = (patch: Partial<QuestionsSearch>) =>
    void navigate({ to: "/admin/questions", search: { ...search, ...patch }, replace: true });
  return (
    <AdminPage
      label={t("admin.label")}
      title={t("admin.questions.title")}
      actions={
        <Link to="/admin/questions/$questionId" params={{ questionId: "new" }} className={CTA}>
          <Plus aria-hidden="true" size={18} />
          {t("admin.questions.new")}
        </Link>
      }
    >
      <div className="flex flex-col gap-4 laptop:flex-row laptop:items-end laptop:justify-between">
        <div className="flex flex-col gap-4">
          <FilterPills
            label={t("admin.field.exam")}
            value={exam}
            options={EXAMS.map((x) => ({ value: x, label: t(EXAM_KEYS[x] ?? x) }))}
            onChange={(v) => set({ exam: v })}
          />
          <FilterPills
            label={t("admin.field.status")}
            value={status}
            options={[
              { value: "all", label: t("admin.filter.all") },
              ...QUESTION_STATUSES.map((s) => ({ value: s, label: t(STATUS_KEYS[s] ?? s) })),
            ]}
            onChange={(v) => set({ status: v === "all" ? undefined : v })}
          />
        </div>
        <form
          className="relative flex min-w-0 laptop:w-80"
          onSubmit={(e) => {
            e.preventDefault();
            set({ q: q.trim() || undefined });
          }}
        >
          <label className="flex w-full">
            <span className="sr-only">{t("admin.questions.search")}</span>
            <Search
              aria-hidden="true"
              size={16}
              className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
            />
            <input
              type="search"
              value={q}
              placeholder={t("admin.questions.search")}
              onChange={(e) => setQ(e.target.value)}
              className={cx(INPUT, "pl-10")}
            />
          </label>
        </form>
      </div>
      {list.isPending ? (
        <Loading rows={5} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <Empty
          title={t("admin.questions.empty")}
          body={t(search.q || status !== "all" ? "admin.state.empty_filtered" : "admin.questions.empty_body")}
        />
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((it) => (
            <li key={it.id}>
              <Link
                to="/admin/questions/$questionId"
                params={{ questionId: it.id }}
                className="flex flex-col gap-2 rounded-block border border-line-soft bg-surface-1 p-4 text-text no-underline hover:border-line-control hover:text-text"
              >
                <span className="line-clamp-2 break-words">{it.stem_text}</span>
                <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
                  <StatusChip status={it.status} prefix={t("admin.field.status")} />
                  {it.difficulty ? <span>{t(DIFFICULTY_KEYS[it.difficulty] ?? it.difficulty)}</span> : null}
                  <span className="font-mono text-label-sm text-text-3">{it.id}</span>
                  <span>{fmtDate(it.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <MoreButton
        show={Boolean(list.hasNextPage)}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
    </AdminPage>
  );
}
