import { Button, cx } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, Plus, Search } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";
import { type AdminChapter, api, CHAPTER_STATUSES, EXAMS, type Exam, qk } from "../api";
import { SourceSelect } from "../components/pickers";
import {
  AdminPage,
  Dialog,
  Empty,
  Failure,
  FilterPills,
  fmtDate,
  INPUT,
  InlineError,
  LABEL,
  Loading,
  STATUS_KEYS,
  StatusChip,
  useT,
} from "../components/ui";
import type { ChaptersSearch } from "../routes";

export const EXAM_KEYS: Record<string, string> = { step1: "admin.exam.step1", step2ck: "admin.exam.step2ck" };
const ID_RE = /^[a-z0-9][a-z0-9_-]{0,127}$/;

function CreateChapterDialog({ open, onClose, exam }: { open: boolean; onClose: () => void; exam: string }) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const ids = useId();
  const [form, setForm] = useState({ id: "", exam, title: "", meta: "", source_id: "" });
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (open) {
      setForm({ id: "", exam, title: "", meta: "", source_id: "" });
      setTried(false);
    }
  }, [open, exam]);
  const create = useMutation({
    mutationFn: () =>
      api.createChapter({
        id: form.id.trim() || undefined,
        exam: form.exam as Exam,
        title: form.title.trim(),
        meta: form.meta.trim() || null,
        source_id: form.source_id,
      }),
    onSuccess: (ch) => {
      void qc.invalidateQueries({ queryKey: qk.all });
      onClose();
      void navigate({ to: "/admin/chapters/$chapterId", params: { chapterId: ch.id } });
    },
  });
  const idBad = form.id.trim() !== "" && !ID_RE.test(form.id.trim());
  const titleBad = !form.title.trim();
  const sourceBad = !form.source_id;
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (idBad || titleBad || sourceBad || create.isPending) return;
    create.mutate();
  }
  return (
    <Dialog open={open} onClose={onClose} title={t("admin.chapters.new_title")}>
      <form noValidate onSubmit={submit} className="flex flex-col gap-5">
        <label htmlFor={`${ids}-t`} className={LABEL}>
          {t("admin.field.title")}
          <input
            id={`${ids}-t`}
            className={INPUT}
            value={form.title}
            maxLength={200}
            aria-invalid={(tried && titleBad) || undefined}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
          {tried && titleBad ? <span className="text-amber">{t("admin.field.required")}</span> : null}
        </label>
        <label htmlFor={`${ids}-i`} className={LABEL}>
          {t("admin.field.id_optional")}
          <input
            id={`${ids}-i`}
            className={cx(INPUT, "font-mono")}
            value={form.id}
            maxLength={128}
            autoCapitalize="off"
            spellCheck={false}
            aria-invalid={idBad || undefined}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
          />
          <span className={idBad ? "text-amber" : "text-text-3"}>{t("admin.field.id_hint")}</span>
        </label>
        <label htmlFor={`${ids}-e`} className={LABEL}>
          {t("admin.field.exam")}
          <select
            id={`${ids}-e`}
            className={INPUT}
            value={form.exam}
            onChange={(e) => setForm({ ...form, exam: e.target.value })}
          >
            {EXAMS.map((x) => (
              <option key={x} value={x}>
                {t(EXAM_KEYS[x] ?? x)}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${ids}-m`} className={LABEL}>
          {t("admin.field.meta")}
          <input
            id={`${ids}-m`}
            className={INPUT}
            value={form.meta}
            maxLength={200}
            onChange={(e) => setForm({ ...form, meta: e.target.value })}
          />
        </label>
        <SourceSelect
          value={form.source_id}
          onChange={(source_id) => setForm({ ...form, source_id })}
          invalid={tried && sourceBad}
        />
        {tried && sourceBad ? <span className="text-ui-sm text-amber">{t("admin.field.source_required")}</span> : null}
        <InlineError error={create.error} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="primary" size="cta" disabled={create.isPending} icon={<Plus size={18} />}>
            {t(create.isPending ? "admin.action.creating" : "admin.chapters.create")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

/** `/admin/chapters`: every shared chapter with status, sections and order; create and reorder. */
export function ChaptersPage({ search }: { search: ChaptersSearch }) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const exam = search.exam ?? "step1";
  const status = search.status ?? "all";
  const [q, setQ] = useState(search.q ?? "");
  const [creating, setCreating] = useState(false);
  const query = { exam, status: status === "all" ? undefined : status, q: search.q };
  const list = useQuery({ queryKey: qk.chapters(query), queryFn: ({ signal }) => api.chapters(query, signal) });
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.reorderChapters({ exam: exam as Exam, ids }),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["admin", "chapters"] }),
  });
  const set = (patch: Partial<ChaptersSearch>) =>
    void navigate({ to: "/admin/chapters", search: { ...search, ...patch }, replace: true });

  const items = list.data?.items ?? [];
  // Reordering is only meaningful on the full, unfiltered list of one exam.
  const canReorder = status === "all" && !search.q && items.length > 1;
  function move(i: number, d: -1 | 1) {
    const next = [...items];
    const [ch] = next.splice(i, 1);
    if (!ch) return;
    next.splice(i + d, 0, ch);
    qc.setQueryData(qk.chapters(query), { items: next });
    reorder.mutate(next.map((c) => c.id));
  }

  return (
    <AdminPage
      label={t("admin.label")}
      title={t("admin.chapters.title")}
      actions={
        <Button variant="primary" size="cta" icon={<Plus size={18} />} onClick={() => setCreating(true)}>
          {t("admin.chapters.new")}
        </Button>
      }
    >
      <div className="flex flex-col gap-4 laptop:flex-row laptop:items-end laptop:justify-between">
        <div className="flex flex-col gap-4 tablet:flex-row tablet:flex-wrap">
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
              ...CHAPTER_STATUSES.map((s) => ({ value: s, label: t(STATUS_KEYS[s] ?? s) })),
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
            <span className="sr-only">{t("admin.chapters.search")}</span>
            <Search
              aria-hidden="true"
              size={16}
              className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
            />
            <input
              type="search"
              value={q}
              placeholder={t("admin.chapters.search")}
              onChange={(e) => setQ(e.target.value)}
              className={cx(INPUT, "pl-10")}
            />
          </label>
        </form>
      </div>
      {reorder.error ? <InlineError error={reorder.error} /> : null}
      {list.isPending ? (
        <Loading rows={4} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <Empty
          title={t("admin.chapters.empty")}
          body={t(search.q || status !== "all" ? "admin.state.empty_filtered" : "admin.chapters.empty_body")}
        />
      ) : (
        <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-busy={reorder.isPending}>
          {items.map((ch: AdminChapter, i) => (
            <li
              key={ch.id}
              className="flex flex-col gap-3 rounded-block border border-line-soft bg-surface-1 p-4 tablet:flex-row tablet:items-center"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <Link
                  to="/admin/chapters/$chapterId"
                  params={{ chapterId: ch.id }}
                  className="font-semibold text-text no-underline hover:underline"
                >
                  {ch.title}
                </Link>
                <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
                  <StatusChip status={ch.status} prefix={t("admin.field.status")} />
                  <span className="font-mono text-label-sm text-text-3">{ch.id}</span>
                  <span>{t("admin.chapters.sections", { n: String(ch.section_count) })}</span>
                  <span>{t("admin.chapters.updated", { date: fmtDate(ch.updated_at) })}</span>
                </span>
              </div>
              {canReorder ? (
                <div className="flex gap-2">
                  <Button
                    aria-label={t("admin.action.move_up", { name: ch.title })}
                    disabled={i === 0 || reorder.isPending}
                    onClick={() => move(i, -1)}
                    icon={<ArrowUp size={16} />}
                  />
                  <Button
                    aria-label={t("admin.action.move_down", { name: ch.title })}
                    disabled={i === items.length - 1 || reorder.isPending}
                    onClick={() => move(i, 1)}
                    icon={<ArrowDown size={16} />}
                  />
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      <CreateChapterDialog open={creating} onClose={() => setCreating(false)} exam={exam} />
    </AdminPage>
  );
}
