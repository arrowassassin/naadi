import type { ExportJob } from "@naadi/shared";
import { Button, Chip, type ChipTone, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Download, FileDown, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { deliverPdf } from "../pdf";
import { useDeleteExport, useExportJobs, useStartExport } from "./exportApi";
import { failedKey, formatBytes, formatUntil, isActive, jobView, startFailure } from "./exportState";
import type { NotesPdfDeps } from "./notesPdf";
import { SettingsPage } from "./SettingsPage";
import { usePdfPrep } from "./usePdfPrep";

const LINK_BTN =
  "inline-flex min-h-12 items-center justify-center gap-2 rounded-pill border border-line-control px-5 text-[0.875rem] text-text no-underline transition-colors duration-(--duration-control) ease-out hover:border-text-3 hover:bg-surface-2 hover:text-text";

const STATUS_TONE: Record<ExportJob["status"], ChipTone> = {
  queued: "planned",
  running: "current",
  ready: "done",
  failed: "critical",
  expired: "meta",
};

function JobRow({ job }: { job: ExportJob }) {
  const t = useUiText();
  const del = useDeleteExport();
  const [confirm, setConfirm] = useState(false);
  const view = jobView(job);
  const size = formatBytes(job.bytes);
  const until = formatUntil(job.expires_at);
  const when = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(job.created_at),
  );
  return (
    <li
      className="flex flex-col gap-4 border-line-soft border-t py-5 first:border-t-0 first:pt-0"
      data-export-job={job.status}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Chip tone={STATUS_TONE[job.status]}>{t(`export.status_${job.status}`)}</Chip>
        <span className="text-text-2 text-ui-sm">{t("export.started", { when })}</span>
        <span className="text-text-3 text-ui-sm">
          {t(job.include_vault ? "export.with_vault" : "export.without_vault")}
        </span>
      </div>

      {isActive(job) ? (
        <div className="flex flex-col gap-3" aria-busy="true">
          <p role="status" className="m-0 text-reading text-text-2">
            {t(job.status === "queued" ? "export.queued_body" : "export.running_body")}
          </p>
          <Skeleton className="h-3 w-full max-w-md rounded-pill" />
        </div>
      ) : null}

      {view.kind === "ready" ? (
        <div className="flex flex-col gap-3">
          <p className="m-0 text-reading text-text-2">
            {[
              size ? t("export.total_size", { size }) : null,
              until ? t("export.available_until", { when: until }) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <ul className="m-0 flex list-none flex-wrap gap-3 p-0">
            {job.downloads.map((d) => (
              <li key={d.part}>
                <a className={LINK_BTN} href={d.url} download data-export-part={d.part}>
                  <Download aria-hidden="true" size={16} />
                  {t(job.parts > 1 ? "export.part_label" : "export.part_single", {
                    part: String(d.part),
                    parts: String(job.parts),
                  })}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {view.kind === "ready_no_links" ? <p className="m-0 text-reading text-text-2">{t("export.no_links")}</p> : null}
      {job.status === "failed" ? (
        <p role="alert" className="m-0 text-reading text-text">
          {t(failedKey(job.error))}
        </p>
      ) : null}
      {job.status === "expired" ? <p className="m-0 text-reading text-text-2">{t("export.expired_body")}</p> : null}

      {!isActive(job) ? (
        <div className="flex flex-col items-start gap-3">
          {confirm ? (
            <div className="flex flex-col gap-3 rounded-block border border-line-strong bg-surface-2 p-4">
              <p className="m-0 text-text">{t("export.delete_confirm")}</p>
              <div className="flex flex-wrap gap-3">
                <Button variant="strong" disabled={del.isPending} onClick={() => del.mutate(job.id)}>
                  {t(del.isPending ? "export.deleting" : "export.delete_yes")}
                </Button>
                <Button variant="secondary" disabled={del.isPending} onClick={() => setConfirm(false)}>
                  {t("account.cancel")}
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="secondary" icon={<Trash2 size={16} />} onClick={() => setConfirm(true)}>
              {t("export.delete")}
            </Button>
          )}
          {del.isError ? (
            <p role="alert" className="m-0 text-amber">
              {t("export.delete_failed")}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function PdfTile({ prep }: { prep: ReturnType<typeof usePdfPrep> }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const { phase } = prep;
  const [failed, setFailed] = useState(false);
  const id = useId();
  const deliver = async (r: Parameters<typeof deliverPdf>[0]) => {
    setFailed(false);
    try {
      await deliverPdf(r);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setFailed(true);
    }
  };
  return (
    <Tile as="section" aria-labelledby={id} className="flex flex-col gap-4" data-pdf-phase={phase.kind}>
      <TileLabel>{t("export.pdf_label")}</TileLabel>
      <h2 id={id} className="m-0 font-display font-bold text-[1.5rem]">
        {t("export.pdf_heading")}
      </h2>
      <p className="m-0 text-reading text-text-2">{t("export.pdf_body")}</p>

      {phase.kind === "preparing" ? (
        <div className="flex flex-col gap-3" aria-busy="true">
          <p role="status" className="m-0 font-semibold text-text">
            {phase.progress && phase.progress.total > 0
              ? t("export.pdf_progress", { done: String(phase.progress.done), total: String(phase.progress.total) })
              : t("export.pdf_preparing")}
          </p>
          {phase.progress && phase.progress.total > 0 ? (
            <progress
              className="h-3 w-full max-w-md accent-[var(--color-paper)]"
              value={phase.progress.done}
              max={phase.progress.total}
              aria-label={t("export.pdf_preparing")}
            />
          ) : (
            <Skeleton className="h-3 w-full max-w-md rounded-pill" />
          )}
          <div>
            <Button variant="secondary" onClick={prep.cancel}>
              {t("export.pdf_cancel")}
            </Button>
          </div>
        </div>
      ) : null}

      {phase.kind === "done" ? (
        <div className="flex flex-col gap-3">
          {phase.outcome.files.length ? (
            <ul className="m-0 flex list-none flex-wrap gap-3 p-0">
              {phase.outcome.files.map((f) => (
                <li key={f.kind}>
                  <button
                    type="button"
                    className={`${LINK_BTN} cursor-pointer bg-transparent`}
                    data-pdf-file={f.kind}
                    onClick={() => void deliver(f.result)}
                  >
                    <FileDown aria-hidden="true" size={16} />
                    {t(`export.pdf_${f.kind}${f.result.pages === 1 ? "_one" : ""}`, {
                      pages: String(f.result.pages),
                    })}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p role="status" className="m-0 text-reading text-text-2">
              {t("export.pdf_nothing")}
            </p>
          )}
          {phase.outcome.files.length && phase.outcome.empty.length ? (
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-text-2 text-ui-sm">
              {phase.outcome.empty.map((e) => (
                <li key={e}>{t(`export.pdf_empty_${e}`)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {phase.kind === "cancelled" ? (
        <p role="status" className="m-0 text-reading text-text-2">
          {t("export.pdf_cancelled")}
        </p>
      ) : null}
      {phase.kind === "failed" ? (
        <p role="alert" className="m-0 text-reading text-text">
          {t("export.pdf_failed")}
        </p>
      ) : null}
      {failed ? (
        <p role="alert" className="m-0 text-reading text-text">
          {t("export.pdf_deliver_failed")}
        </p>
      ) : null}

      {phase.kind !== "preparing" ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" disabled={!online} onClick={() => void prep.start()}>
            {t(phase.kind === "idle" ? "export.pdf_make" : "export.pdf_make_again")}
          </Button>
          {!online ? <span className="text-text-3 text-ui-sm">{t("export.pdf_offline")}</span> : null}
        </div>
      ) : null}
    </Tile>
  );
}

/** Settings -> Data & export: one lime CTA; the server builds the ZIP, this device builds the notes PDFs. */
export function DataExportPage({ pdfDeps }: { pdfDeps?: (t: ReturnType<typeof useUiText>) => NotesPdfDeps } = {}) {
  const t = useUiText();
  const online = useOnlineStatus();
  const boxId = useId();
  const [vault, setVault] = useState(false);
  const { list, active } = useExportJobs();
  const start = useStartExport();
  const prep = usePdfPrep(pdfDeps);
  const jobs = list.data ?? [];
  const failure = start.error ? startFailure(start.error) : null;
  const busy = start.isPending || active !== undefined;

  const run = () => {
    if (busy || !online) return;
    start.mutate(vault, {
      // One export at a time: the device PDFs are still worth making next to the job that is already running.
      onError: (e) => {
        if (startFailure(e) === "active") void prep.start();
      },
      onSuccess: () => void prep.start(),
    });
  };

  return (
    <SettingsPage group="data" titleKey="settings.data_title" headingId="data-settings-h">
      <div className="grid items-start gap-5 laptop:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-5">
          <Tile as="section" aria-labelledby="export-h" className="flex flex-col gap-6">
            <h2 id="export-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("export.heading")}
            </h2>
            <p className="m-0 text-reading text-text-2">{t("export.intro")}</p>
            <div className="flex items-start gap-4 border-line-soft border-t pt-5">
              <input
                id={boxId}
                type="checkbox"
                checked={vault}
                onChange={(e) => setVault(e.target.checked)}
                aria-describedby={`${boxId}-d`}
                className="mt-1 size-[26px] shrink-0 accent-[var(--color-paper)]"
              />
              <span className="flex min-w-0 flex-col gap-1">
                <label htmlFor={boxId} className="inline-flex min-h-11 items-center font-semibold text-[1.0625rem]">
                  {t("export.vault_label")}
                </label>
                <span id={`${boxId}-d`} className="text-reading text-text-2">
                  {t("export.vault_note")}
                </span>
              </span>
            </div>
            {failure ? (
              <p role="alert" className="m-0 font-semibold text-amber" data-export-error={failure}>
                {t(`export.start_${failure}`)}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-4">
              <Button
                variant="primary"
                size="cta"
                disabled={busy || !online}
                aria-describedby={!online ? `${boxId}-off` : undefined}
                onClick={run}
              >
                {t(busy ? "export.cta_busy" : "export.cta")}
              </Button>
              {!online ? (
                <span id={`${boxId}-off`} className="text-text-3">
                  {t("export.offline")}
                </span>
              ) : null}
            </div>
          </Tile>
          <PdfTile prep={prep} />
        </div>

        <Tile as="section" aria-labelledby="exports-h" className="flex flex-col gap-5">
          <h2 id="exports-h" className="m-0 font-display font-bold text-[1.5rem]">
            {t("export.jobs_heading")}
          </h2>
          {list.isPending && !list.data ? (
            <div aria-busy="true" className="flex flex-col gap-3">
              <p role="status" className="sr-only">
                {t("export.jobs_loading")}
              </p>
              <Skeleton className="h-6 w-40" />
              <Skeleton className="h-20 rounded-block" />
            </div>
          ) : !list.data ? (
            <ErrorState
              title={t("export.jobs_failed_title")}
              message={t(
                list.error instanceof ApiError && list.error.code === "export_unavailable"
                  ? "export.start_unavailable"
                  : "export.jobs_failed",
              )}
              requestId={list.error instanceof ApiError ? list.error.requestId : null}
              retryLabel={t("settings.retry")}
              onRetry={() => void list.refetch()}
              retrying={list.isFetching}
            />
          ) : jobs.length === 0 ? (
            <p className="m-0 text-reading text-text-2" data-export-empty="">
              {t("export.jobs_empty")}
            </p>
          ) : (
            <ul className="m-0 flex list-none flex-col p-0">
              {jobs.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </ul>
          )}
        </Tile>
      </div>
    </SettingsPage>
  );
}
