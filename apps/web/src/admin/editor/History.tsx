import { Button, Chip, cx } from "@naadi/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { History as HistoryIcon, Minus, Plus, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api, type DiffBlock, qk, type Revision, type RevisionSummary } from "../api";
import { Empty, Failure, fmtDateTime, InlineError, Loading, StatusChip, useT } from "../components/ui";

const ORIGIN_KEYS: Record<string, string> = {
  admin: "admin.revision.origin.admin",
  seed: "admin.revision.origin.seed",
  rollback: "admin.revision.origin.rollback",
  authoring: "admin.revision.origin.authoring",
  assistant: "admin.revision.origin.assistant",
};

/** A block diff from the server, in words and icons (added / removed), never by colour alone. */
export function DiffView({ blocks }: { blocks: DiffBlock[] }) {
  const t = useT();
  const changed = blocks.filter((b) => b.op !== "equal").length;
  if (changed === 0) return <p className="m-0 text-text-2">{t("admin.diff.no_changes")}</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label={t("admin.diff.label")}>
      {blocks.map((b, i) => {
        if (b.op === "equal") {
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: diff rows are positional
            <li key={i} className="px-3 text-ui-sm text-text-3">
              <span className="sr-only">{t("admin.diff.equal")}: </span>
              <span className="line-clamp-1">{b.text}</span>
            </li>
          );
        }
        const ins = b.op === "insert";
        return (
          <li
            // biome-ignore lint/suspicious/noArrayIndexKey: diff rows are positional
            key={i}
            className={cx(
              "flex flex-col gap-1 rounded-inner border px-3 py-2",
              ins ? "border-text-2 bg-surface-2" : "border-dashed border-line-dashed",
            )}
          >
            <span className="flex items-center gap-2 font-mono text-label-sm uppercase">
              {ins ? <Plus aria-hidden="true" size={13} /> : <Minus aria-hidden="true" size={13} />}
              {t(ins ? "admin.diff.insert" : "admin.diff.delete")}
              <span className="text-text-3">{b.kind}</span>
            </span>
            <span className={cx("break-words text-ui-sm", ins ? "text-text" : "text-text-2 line-through")}>
              {b.text}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function RevisionRow({
  rev,
  live,
  previous,
  onRollback,
  busy,
}: {
  rev: RevisionSummary;
  live: number | null;
  previous: RevisionSummary | null;
  onRollback: (r: RevisionSummary) => void;
  busy: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [against, setAgainst] = useState<"live" | "previous">(
    live !== null && live !== rev.id && !rev.is_live ? "live" : "previous",
  );
  const target = against === "live" ? "live" : previous ? String(previous.id) : null;
  const diff = useQuery({
    queryKey: qk.diff(rev.id, target ?? "none"),
    enabled: open && target !== null,
    queryFn: ({ signal }) => api.revisionDiff(rev.id, target ?? "live", signal),
  });
  const isLive = rev.is_live || rev.id === live;
  return (
    <li className="flex flex-col gap-3 border-line-soft border-t py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={rev.status} />
        {isLive ? <Chip tone="current">{t("admin.revision.live")}</Chip> : null}
        <span className="text-ui-sm text-text-2">{fmtDateTime(rev.published_at ?? rev.created_at)}</span>
        <span className="text-ui-sm text-text-3">
          {t(ORIGIN_KEYS[rev.origin] ?? "admin.revision.origin.other")} ·{" "}
          {t("admin.revision.words", { n: String(rev.word_count) })}
        </span>
      </div>
      {rev.note ? <p className="m-0 break-words text-ui-sm text-text-2">{rev.note}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {t(open ? "admin.revision.hide_diff" : "admin.revision.show_diff")}
        </Button>
        {isLive ? null : (
          <Button icon={<RotateCcw size={16} />} disabled={busy} onClick={() => onRollback(rev)}>
            {t("admin.revision.rollback")}
          </Button>
        )}
      </div>
      {open ? (
        <div className="flex flex-col gap-3 rounded-block border border-line-soft bg-surface-1 p-3">
          <div className="flex flex-wrap gap-2">
            {live !== null && !isLive ? (
              <Button aria-pressed={against === "live"} onClick={() => setAgainst("live")}>
                {t("admin.revision.against_live")}
              </Button>
            ) : null}
            {previous ? (
              <Button aria-pressed={against === "previous"} onClick={() => setAgainst("previous")}>
                {t("admin.revision.against_previous")}
              </Button>
            ) : null}
          </div>
          {target === null ? (
            <p className="m-0 text-text-2">{t("admin.revision.first")}</p>
          ) : diff.isPending ? (
            <Loading rows={2} />
          ) : diff.isError ? (
            <Failure error={diff.error} onRetry={() => void diff.refetch()} retrying={diff.isFetching} />
          ) : (
            <DiffView blocks={diff.data.blocks} />
          )}
        </div>
      ) : null}
    </li>
  );
}

/** Revision history of a section: every saved and published body, a diff, and rollback (a new draft). */
export function History({
  sectionId,
  liveRevisionId,
  onRolledBack,
  confirmDiscard,
}: {
  sectionId: string;
  liveRevisionId: number | null;
  onRolledBack: (r: Revision) => void;
  /** Called before a rollback replaces unsaved local edits; false cancels. */
  confirmDiscard: () => boolean;
}) {
  const t = useT();
  const list = useQuery({
    queryKey: qk.revisions(sectionId),
    queryFn: ({ signal }) => api.revisions(sectionId, signal),
  });
  const rollback = useMutation({
    mutationFn: (r: RevisionSummary) => api.rollback(r.id, null),
    onSuccess: (rev) => onRolledBack(rev),
  });
  return (
    <section aria-labelledby="history-h" className="flex flex-col gap-3">
      <h2 id="history-h" className="m-0 flex items-center gap-2 font-display font-bold text-[1.25rem]">
        <HistoryIcon aria-hidden="true" size={18} />
        {t("admin.revision.title")}
      </h2>
      <InlineError error={rollback.error} />
      {list.isPending ? (
        <Loading rows={3} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <Empty title={t("admin.revision.empty")} />
      ) : (
        <ol className="m-0 flex list-none flex-col p-0">
          {list.data.items.map((r, i) => (
            <RevisionRow
              key={r.id}
              rev={r}
              live={liveRevisionId}
              previous={list.data.items[i + 1] ?? null}
              busy={rollback.isPending}
              onRollback={(x) => {
                if (confirmDiscard()) rollback.mutate(x);
              }}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
