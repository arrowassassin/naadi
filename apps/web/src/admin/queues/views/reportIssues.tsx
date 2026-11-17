import { StatusChip, useT } from "../../components/ui";
import { REPORT_REF_KEYS, ReportLinkTo } from "../../pages/ReportsPage";
import { type QueueViewProps, registerQueueView } from "../registry";

/*
 * report_issues: a student's "Report an issue" (§12). Approve = resolved, reject = dismissed (server side).
 * detail: {ref_type, status, body, chapter_id, section_id, question_id}
 */

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function Row({ item }: QueueViewProps) {
  const t = useT();
  const refType = str(item.detail?.ref_type) ?? "other";
  const body = str(item.detail?.body);
  const status = str(item.detail?.status);
  return (
    <span className="flex min-w-0 flex-col gap-1.5">
      <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
        {status ? <StatusChip status={status} /> : null}
        <span>{t(REPORT_REF_KEYS[refType] ?? "admin.ref.other")}</span>
      </span>
      {body ? <span className="line-clamp-2 break-words text-text">{body}</span> : null}
    </span>
  );
}

function Detail({ item }: QueueViewProps) {
  const body = str(item.detail?.body);
  return (
    <div className="flex flex-col gap-3">
      {body ? <p className="m-0 whitespace-pre-wrap break-words text-text">{body}</p> : null}
      <ReportLinkTo
        link={{
          chapter_id: str(item.detail?.chapter_id),
          section_id: str(item.detail?.section_id),
          question_id: str(item.detail?.question_id),
        }}
      />
    </div>
  );
}

registerQueueView("report_issues", {
  titleKey: "admin.queues.report_issues.title",
  Row,
  Detail,
  rejectNeedsNote: false,
});
