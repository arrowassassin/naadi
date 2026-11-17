import { Chip } from "@naadi/ui";
import { CircleAlert, ExternalLink, Link2Off, MoveRight } from "lucide-react";
import { useT } from "../../components/ui";
import { type QueueViewProps, registerQueueView } from "../registry";

/*
 * pathway_checks (segment 17): the weekly official-page check found a task's page changed, unreachable or moved.
 * Approve = the admin confirms the task is still right (last verified today); reject = this check is noise.
 * detail: {task_id, stage_key, url, http_status, status, summary_key, quotes[{text, url}], last_verified}
 */

const STATUS: Record<string, { key: string; Icon: typeof CircleAlert }> = {
  changed: { key: "admin.queues.pathway_checks.status.changed", Icon: CircleAlert },
  unreachable: { key: "admin.queues.pathway_checks.status.unreachable", Icon: Link2Off },
  moved: { key: "admin.queues.pathway_checks.status.moved", Icon: MoveRight },
};

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function StatusWord({ status }: { status: string | null }) {
  const t = useT();
  const look = status ? STATUS[status] : undefined;
  if (!look) return null;
  return (
    <Chip tone="meta" icon={<look.Icon size={13} aria-hidden="true" />}>
      {t(look.key)}
    </Chip>
  );
}

function Row({ item }: QueueViewProps) {
  const t = useT();
  const verified = str(item.detail?.last_verified);
  return (
    <span className="flex min-w-0 flex-col gap-1.5">
      <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
        <StatusWord status={str(item.detail?.status)} />
        <span>
          {verified
            ? t("admin.queues.pathway_checks.last_verified", { date: verified })
            : t("admin.queues.pathway_checks.never_verified")}
        </span>
      </span>
      {str(item.detail?.url) ? (
        <span className="break-all font-mono text-label-sm text-text-3">{str(item.detail?.url)}</span>
      ) : null}
    </span>
  );
}

function Detail({ item }: QueueViewProps) {
  const t = useT();
  const url = str(item.detail?.url);
  const quotes = Array.isArray(item.detail?.quotes) ? item.detail.quotes : [];
  return (
    <div className="flex flex-col gap-3">
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 break-all">
          <ExternalLink aria-hidden="true" size={16} />
          {t("admin.queues.pathway_checks.open_page")}
        </a>
      ) : null}
      {quotes.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {quotes.map((q, i) => {
            const text =
              q && typeof q === "object" && !Array.isArray(q) ? str((q as Record<string, unknown>).text) : null;
            return text ? (
              // biome-ignore lint/suspicious/noArrayIndexKey: quotes have no id and never reorder
              <li key={i} className="border-l-2 border-line pl-3 text-text-2">
                {text}
              </li>
            ) : null;
          })}
        </ul>
      ) : null}
    </div>
  );
}

registerQueueView("pathway_checks", {
  titleKey: "admin.queues.pathway_checks.title",
  Row,
  Detail,
  rejectNeedsNote: false,
});
