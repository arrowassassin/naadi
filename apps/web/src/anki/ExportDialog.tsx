import { Button } from "@naadi/ui";
import { useId, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { errorKey, exportDeck, useAnkiDecks, useDeleteImportedDeck } from "./api";
import { Modal } from "./Dialog";

const ALL = "";

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function ExportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useUiText();
  const ids = useId();
  const decks = useAnkiDecks(open);
  const remove = useDeleteImportedDeck();
  const [deck, setDeck] = useState(ALL);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const picked = decks.data?.decks.find((d) => d.deck === deck);
  const empty = decks.data?.total_cards === 0;

  function close() {
    if (busy) return;
    setMessage(null);
    setConfirm(false);
    setDeck(ALL);
    onClose();
  }

  async function run() {
    setBusy(true);
    setMessage(null);
    try {
      const { blob, filename } = await exportDeck(deck === ALL ? null : deck);
      download(blob, filename);
      setMessage({ kind: "ok", text: t("anki.exported", { file: filename }) });
    } catch (err) {
      setMessage({ kind: "error", text: t(errorKey(err)) });
    } finally {
      setBusy(false);
    }
  }

  async function removeImported() {
    try {
      const out = await remove.mutateAsync(deck);
      setConfirm(false);
      setDeck(ALL);
      setMessage({ kind: "ok", text: t("anki.deleted", { n: String(out.deleted) }) });
    } catch (err) {
      setMessage({ kind: "error", text: t(errorKey(err)) });
    }
  }

  return (
    <Modal
      open={open}
      titleId={`${ids}-title`}
      title={t("anki.export_title")}
      closeLabel={t("anki.close")}
      busy={busy}
      onClose={close}
    >
      <p className="m-0 text-reading text-text-2">{t("anki.export_hint")}</p>
      <label htmlFor={`${ids}-deck`} className="flex flex-col gap-2 text-text-2 text-ui-sm">
        {t("anki.export_deck_label")}
        <select
          id={`${ids}-deck`}
          value={deck}
          disabled={busy || decks.isPending}
          onChange={(e) => {
            setDeck(e.target.value);
            setConfirm(false);
            setMessage(null);
          }}
          className="min-h-12 w-full rounded-block border border-line-control bg-surface-2 px-4 text-text text-ui"
        >
          <option value={ALL}>{t("anki.export_all")}</option>
          {decks.data?.decks.map((d) => (
            <option key={d.deck} value={d.deck}>
              {t("anki.export_deck_count", { deck: d.deck, n: String(d.cards) })}
            </option>
          ))}
        </select>
      </label>
      {decks.isError ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t(errorKey(decks.error))}
        </p>
      ) : null}
      {message ? (
        <p
          role={message.kind === "error" ? "alert" : "status"}
          className={message.kind === "error" ? "m-0 text-amber text-ui-sm" : "m-0 text-ui-sm"}
        >
          {message.text}
        </p>
      ) : null}
      {busy ? (
        <p role="status" className="m-0 text-text-2 text-ui-sm">
          {t("anki.exporting")}
        </p>
      ) : null}
      {confirm && picked ? (
        <div
          role="alertdialog"
          aria-label={t("anki.delete_deck")}
          className="flex flex-col gap-3 rounded-block border border-amber p-4"
        >
          <p className="m-0 text-ui">{t("anki.delete_confirm", { n: String(picked.imported), deck: picked.deck })}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="strong" onClick={() => void removeImported()} disabled={remove.isPending}>
              {t("anki.delete_deck")}
            </Button>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              {t("anki.close")}
            </Button>
          </div>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button variant="primary" onClick={() => void run()} disabled={busy || decks.isPending || empty === true}>
          {t("anki.export_button")}
        </Button>
        {picked && picked.imported > 0 && !confirm ? (
          <Button variant="secondary" onClick={() => setConfirm(true)} disabled={busy}>
            {t("anki.delete_deck")}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={close} disabled={busy}>
          {t("anki.close")}
        </Button>
      </div>
    </Modal>
  );
}
