import { Button, Chip } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowUpRight, BookOpen } from "lucide-react";
import { useId, useState } from "react";
import { api, qk, REPORT_STATUSES, type ReportLink, type ReportRow, type ReportStatus } from "../api";
import {
  AdminPage,
  Empty,
  Failure,
  FilterPills,
  fmtDateTime,
  INPUT,
  InlineError,
  LABEL,
  Loading,
  STATUS_KEYS,
  StatusChip,
  TEXTAREA,
  useT,
} from "../components/ui";
import type { StatusSearch } from "../routes";

export const REPORT_REF_KEYS: Record<string, string> = {
  section: "admin.ref.section",
  question: "admin.ref.question",
  flashcard: "admin.ref.flashcard",
  media: "admin.ref.media",
  chapter: "admin.ref.chapter",
  pathway_task: "admin.ref.pathway_task",
  assistant_message: "admin.ref.assistant_message",
  other: "admin.ref.other",
};

const LINK = "inline-flex min-h-11 items-center gap-1.5 text-ui-sm text-text";

/** Deep links from a report to the item: the admin page to fix it, and the reader where the student saw it. */
export function ReportLinkTo({ link }: { link: ReportLink }) {
  const t = useT();
  const out = [];
  if (link.section_id) {
    out.push(
      <Link key="s" to="/admin/sections/$sectionId/edit" params={{ sectionId: link.section_id }} className={LINK}>
        {t("admin.reports.open_section")}
        <ArrowUpRight aria-hidden="true" size={14} />
      </Link>,
    );
    if (link.chapter_id) {
      out.push(
        <Link
          key="r"
          to="/learn/$chapterId/$sectionId"
          params={{ chapterId: link.chapter_id, sectionId: link.section_id }}
          className={LINK}
        >
          <BookOpen aria-hidden="true" size={14} />
          {t("admin.reports.open_reader")}
        </Link>,
      );
    }
  } else if (link.chapter_id) {
    out.push(
      <Link key="c" to="/admin/chapters/$chapterId" params={{ chapterId: link.chapter_id }} className={LINK}>
        {t("admin.reports.open_chapter")}
        <ArrowUpRight aria-hidden="true" size={14} />
      </Link>,
    );
  }
  if (link.question_id) {
    out.push(
      <Link key="q" to="/admin/questions/$questionId" params={{ questionId: link.question_id }} className={LINK}>
        {t("admin.reports.open_question")}
        <ArrowUpRight aria-hidden="true" size={14} />
      </Link>,
    );
  }
  if (link.media_id) {
    out.push(
      <Link key="m" to="/admin/media" search={{ q: link.media_id }} className={LINK}>
        {t("admin.reports.open_media")}
        <ArrowUpRight aria-hidden="true" size={14} />
      </Link>,
    );
  }
  if (!out.length) return <span className="text-ui-sm text-text-3">{t("admin.reports.no_link")}</span>;
  return <span className="flex flex-wrap gap-x-4">{out}</span>;
}

function Report({ r }: { r: ReportRow }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const [status, setStatus] = useState<ReportStatus>(r.status === "open" ? "triaged" : r.status);
  const [note, setNote] = useState("");
  const update = useMutation({
    mutationFn: () => api.reportStatus(r.id, { status, note: note.trim() || null }),
    onSuccess: () => {
      setNote("");
      void qc.invalidateQueries({ queryKey: ["admin", "reports"] });
      void qc.invalidateQueries({ queryKey: qk.queues });
    },
  });
  return (
    <li
      className="flex flex-col gap-3 rounded-block border border-line-soft bg-surface-1 p-4 tablet:p-5"
      data-report-id={r.id}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={r.status} prefix={t("admin.field.status")} />
        <Chip tone="meta">{t(REPORT_REF_KEYS[r.ref_type] ?? "admin.ref.other")}</Chip>
        {r.ref_id ? <span className="font-mono text-label-sm break-all text-text-3">{r.ref_id}</span> : null}
        <span className="text-ui-sm text-text-2">{fmtDateTime(r.created_at)}</span>
      </div>
      <p className="m-0 whitespace-pre-wrap break-words text-text">{r.body}</p>
      <ReportLinkTo link={r.link} />
      <form
        noValidate
        className="grid items-end gap-3 tablet:grid-cols-[12rem_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          if (status !== r.status && !update.isPending) update.mutate();
        }}
      >
        <label htmlFor={`${ids}-s`} className={LABEL}>
          {t("admin.reports.new_status")}
          <select
            id={`${ids}-s`}
            className={INPUT}
            value={status}
            onChange={(e) => setStatus(e.target.value as ReportStatus)}
          >
            {REPORT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {t(STATUS_KEYS[s] ?? s)}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${ids}-n`} className={LABEL}>
          {t("admin.field.note_optional")}
          <textarea
            id={`${ids}-n`}
            className={`${TEXTAREA} min-h-12`}
            maxLength={1000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <Button type="submit" variant="strong" disabled={status === r.status || update.isPending}>
          {t(update.isPending ? "admin.action.saving" : "admin.reports.update")}
        </Button>
      </form>
      <InlineError error={update.error} />
    </li>
  );
}

/** `/admin/reports`: "Report an issue" triage (open → triaged → resolved/dismissed) with deep links. */
export function ReportsPage({ search }: { search: StatusSearch }) {
  const t = useT();
  const navigate = useNavigate();
  const status = search.status ?? "open";
  const list = useQuery({
    queryKey: qk.reports(status === "all" ? undefined : status),
    queryFn: ({ signal }) => api.reports(status === "all" ? undefined : status, signal),
  });
  const counts = list.data?.counts ?? {};
  const label = (s: string) =>
    counts[s] === undefined
      ? t(STATUS_KEYS[s] ?? s)
      : t("admin.reports.filter_count", { status: t(STATUS_KEYS[s] ?? s), n: String(counts[s]) });
  return (
    <AdminPage label={t("admin.label")} title={t("admin.reports.title")}>
      <FilterPills
        label={t("admin.field.status")}
        value={status}
        options={[
          ...REPORT_STATUSES.map((s) => ({ value: s, label: label(s) })),
          { value: "all", label: t("admin.filter.all") },
        ]}
        onChange={(v) => void navigate({ to: "/admin/reports", search: { status: v }, replace: true })}
      />
      {list.isPending ? (
        <Loading rows={3} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <Empty title={t("admin.reports.empty")} />
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {list.data.items.map((r) => (
            <Report key={`${r.id}:${r.status}`} r={r} />
          ))}
        </ul>
      )}
    </AdminPage>
  );
}
