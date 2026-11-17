import type { ImportReport } from "@naadi/shared";
import { Button } from "@naadi/ui";
import { CircleAlert, FileUp } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { errorKey, importDeck, useInvalidateCards } from "./api";
import { Modal } from "./Dialog";

type Phase =
  | { name: "idle" }
  | { name: "busy" }
  | { name: "done"; report: ImportReport }
  | { name: "error"; key: string };

/** The report of one import: counts only, worded from `anki.report_*` copy. Status is words, never colour alone. */
export function ImportReportView({ report, t }: { report: ImportReport; t: ReturnType<typeof useUiText> }) {
  const n = (v: number) => String(v);
  const lines: string[] = [t("anki.report_cards_added", { n: n(report.cards_added) })];
  if (report.cards_existing > 0) lines.push(t("anki.report_cards_existing", { n: n(report.cards_existing) }));
  if (report.cards_skipped > 0) lines.push(t("anki.report_cards_skipped", { n: n(report.cards_skipped) }));
  if (report.images_added > 0) lines.push(t("anki.report_images_added", { n: n(report.images_added) }));
  if (report.images_skipped > 0) lines.push(t("anki.report_images_skipped", { n: n(report.images_skipped) }));
  if (report.audio_skipped > 0) lines.push(t("anki.report_audio_skipped", { n: n(report.audio_skipped) }));
  return (
    <div data-testid="anki-report" className="flex flex-col gap-4">
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-ui">
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      {report.media_manifest === "unreadable" ? (
        <p role="note" className="m-0 flex items-start gap-2 text-amber text-ui-sm">
          <CircleAlert aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
          <span>{t("anki.report_manifest_unreadable")}</span>
        </p>
      ) : null}
      {report.decks.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 className="m-0 font-semibold text-ui">{t("anki.report_decks")}</h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-text-2 text-ui-sm">
            {report.decks.map((d) => (
              <li key={d.deck}>
                {t("anki.report_deck_line", { deck: d.deck, added: n(d.added), existing: n(d.existing) })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function ImportDialog({
  open,
  onClose,
  onReview,
}: {
  open: boolean;
  onClose: () => void;
  /** "Review these cards": the host decides what that means (refetch here, navigate from the Library). */
  onReview: () => void;
}) {
  const t = useUiText();
  const ids = useId();
  const invalidate = useInvalidateCards();
  const [file, setFile] = useState<File | null>(null);
  const [prefix, setPrefix] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const busy = phase.name === "busy";

  function close() {
    if (busy) return;
    setFile(null);
    setPrefix("");
    setPhase({ name: "idle" });
    onClose();
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || busy) return;
    setPhase({ name: "busy" });
    try {
      const report = await importDeck(file, prefix.trim() || null);
      invalidate();
      setPhase({ name: "done", report });
    } catch (err) {
      setPhase({ name: "error", key: errorKey(err) });
    }
  }

  const field =
    "min-h-12 w-full rounded-block border border-line-control bg-surface-2 px-4 text-ui text-text placeholder:text-text-3";
  return (
    <Modal
      open={open}
      titleId={`${ids}-title`}
      title={phase.name === "done" ? t("anki.import_done_title") : t("anki.import_title")}
      closeLabel={t("anki.close")}
      busy={busy}
      onClose={close}
    >
      {phase.name === "done" ? (
        <>
          <ImportReportView report={phase.report} t={t} />
          <div className="flex flex-wrap gap-3">
            {phase.report.cards_added + phase.report.cards_existing > 0 ? (
              <Button
                variant="primary"
                onClick={() => {
                  close();
                  onReview();
                }}
              >
                {t("anki.report_review")}
              </Button>
            ) : null}
            <Button variant="secondary" onClick={close}>
              {t("anki.report_close")}
            </Button>
          </div>
        </>
      ) : (
        <form onSubmit={submit} noValidate className="flex flex-col gap-5" aria-busy={busy}>
          <p className="m-0 text-reading text-text-2">{t("anki.import_hint")}</p>
          <label
            htmlFor={`${ids}-file`}
            className="flex min-h-14 cursor-pointer items-center gap-3 rounded-block border border-line-control border-dashed bg-surface-2 px-4 text-ui"
          >
            <FileUp aria-hidden="true" size={20} className="shrink-0" />
            <span className="min-w-0 break-all">{file ? file.name : t("anki.import_choose")}</span>
            <input
              id={`${ids}-file`}
              type="file"
              accept=".apkg"
              disabled={busy}
              className="sr-only"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                if (phase.name === "error") setPhase({ name: "idle" });
              }}
            />
          </label>
          <label htmlFor={`${ids}-prefix`} className="flex flex-col gap-2 text-text-2 text-ui-sm">
            {t("anki.import_deck_name")}
            <input
              id={`${ids}-prefix`}
              className={field}
              value={prefix}
              maxLength={40}
              disabled={busy}
              placeholder={t("anki.import_deck_name_hint")}
              onChange={(e) => setPrefix(e.target.value)}
            />
          </label>
          {phase.name === "error" ? (
            <div role="alert" className="flex flex-col gap-1 text-amber text-ui-sm">
              <strong>{t("anki.error_title")}</strong>
              <span>{t(phase.key)}</span>
            </div>
          ) : null}
          {busy ? (
            <p role="status" className="m-0 text-text-2 text-ui-sm">
              {t("anki.importing")}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <Button type="submit" variant="primary" disabled={!file || busy}>
              {t("anki.import_action")}
            </Button>
            <Button variant="secondary" onClick={close} disabled={busy}>
              {t("anki.close")}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
