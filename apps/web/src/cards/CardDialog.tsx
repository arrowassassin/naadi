import type { CardOut } from "@naadi/shared";
import { Button } from "@naadi/ui";
import { X } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { errorKey, useCreateCard, useDeleteCard, useUpdateCard } from "./api";
import { useCardsCopy } from "./copy";

export const MAX_FACE = 4000;

export interface CardDraft {
  front_text: string;
  back_text: string;
  deck?: string;
  question_id?: string;
  section_id?: string;
}

export interface CardDialogProps {
  open: boolean;
  /** Edit this card; otherwise create one from `draft`. */
  card?: CardOut | null;
  draft?: CardDraft;
  onClose: () => void;
  onSaved?: (card: CardOut) => void;
  onDeleted?: (id: string) => void;
}

function faceError(v: string, t: ReturnType<typeof useCardsCopy>): string | null {
  const text = v.trim();
  if (!text) return t("cards.field_required");
  if (text.length > MAX_FACE) return t("cards.field_too_long", { max: MAX_FACE });
  return null;
}

/**
 * Create or edit a card: Front, Back (plain text; a blank line starts a paragraph) and Deck. Only the student's
 * own cards can be edited (the server checks too). Delete asks first.
 */
export function CardDialog({ open, card, draft, onClose, onSaved, onDeleted }: CardDialogProps) {
  const t = useCardsCopy();
  const ref = useRef<HTMLDialogElement>(null);
  const ids = useId();
  const create = useCreateCard();
  const update = useUpdateCard();
  const remove = useDeleteCard();
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [deck, setDeck] = useState("");
  const [touched, setTouched] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const editing = Boolean(card);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setFront(card?.front_text ?? draft?.front_text ?? "");
      setBack(card?.back_text ?? draft?.back_text ?? "");
      setDeck(card?.deck ?? draft?.deck ?? t("cards.deck_default"));
      setTouched(false);
      setConfirmDelete(false);
      create.reset();
      update.reset();
      remove.reset();
      d.showModal();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open, card, draft, create.reset, update.reset, remove.reset, t]);

  const frontError = touched ? faceError(front, t) : null;
  const backError = touched ? faceError(back, t) : null;
  const busy = create.isPending || update.isPending || remove.isPending;
  const failure = create.error ?? update.error ?? remove.error;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (faceError(front, t) || faceError(back, t) || busy) return;
    const deckName = deck.trim() || t("cards.deck_default");
    try {
      const saved = card
        ? await update.mutateAsync({ id: card.id, body: { front_text: front, back_text: back, deck: deckName } })
        : await create.mutateAsync({
            front_text: front,
            back_text: back,
            deck: deckName,
            question_id: draft?.question_id,
            section_id: draft?.section_id,
          });
      onSaved?.(saved);
      onClose();
    } catch {
      // shown below from the mutation's error
    }
  }

  async function confirmRemove() {
    if (!card || busy) return;
    try {
      await remove.mutateAsync(card.id);
      onDeleted?.(card.id);
      onClose();
    } catch {
      // shown below
    }
  }

  const field =
    "w-full rounded-block border border-line-control bg-surface-2 px-4 py-3 text-ui text-text placeholder:text-text-3 focus:border-text-3 focus:outline-none";

  return (
    <dialog
      ref={ref}
      aria-labelledby={`${ids}-title`}
      onClose={onClose}
      onCancel={(e) => {
        if (busy) e.preventDefault();
      }}
      className="m-auto w-[min(100%-32px,640px)] max-h-[calc(100dvh-32px)] overflow-y-auto rounded-tile-sm border border-line-strong bg-surface-1 p-0 text-text backdrop:bg-ground/70 backdrop:backdrop-blur-sm"
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-5 p-6 tablet:p-8">
        <div className="flex items-start justify-between gap-4">
          <h2 id={`${ids}-title`} className="m-0 font-display text-lead">
            {editing ? t("cards.dialog_edit") : t("cards.dialog_new")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t("cards.cancel")}
            className="flex size-11 shrink-0 items-center justify-center rounded-full border border-line-control bg-control text-text disabled:opacity-40"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        <p id={`${ids}-hint`} className="m-0 text-text-3 text-ui-sm">
          {t("cards.field_hint")}
        </p>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-front`} className="font-mono text-label text-text-3 uppercase">
            {t("cards.field_front")}
          </label>
          <textarea
            id={`${ids}-front`}
            value={front}
            onChange={(e) => setFront(e.target.value)}
            rows={3}
            maxLength={MAX_FACE * 2}
            aria-invalid={frontError ? true : undefined}
            aria-describedby={`${ids}-hint${frontError ? ` ${ids}-front-error` : ""}`}
            className={field}
          />
          {frontError ? (
            <p id={`${ids}-front-error`} className="m-0 text-amber text-ui-sm">
              {frontError}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-back`} className="font-mono text-label text-text-3 uppercase">
            {t("cards.field_back")}
          </label>
          <textarea
            id={`${ids}-back`}
            value={back}
            onChange={(e) => setBack(e.target.value)}
            rows={4}
            maxLength={MAX_FACE * 2}
            aria-invalid={backError ? true : undefined}
            aria-describedby={`${ids}-hint${backError ? ` ${ids}-back-error` : ""}`}
            className={field}
          />
          {backError ? (
            <p id={`${ids}-back-error`} className="m-0 text-amber text-ui-sm">
              {backError}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-deck`} className="font-mono text-label text-text-3 uppercase">
            {t("cards.field_deck")}
          </label>
          <input
            id={`${ids}-deck`}
            value={deck}
            onChange={(e) => setDeck(e.target.value)}
            maxLength={60}
            className={field}
          />
        </div>
        {failure ? (
          <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text">
            {t(errorKey(failure))}
            {failure instanceof ApiError && failure.requestId ? (
              <span className="block font-mono text-label-sm text-text-3">{failure.requestId}</span>
            ) : null}
          </p>
        ) : null}
        {confirmDelete ? (
          <div className="flex flex-col gap-3 rounded-block border border-amber/60 p-4">
            <p role="alert" className="m-0 text-text">
              {t("cards.delete_confirm")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="strong" onClick={confirmRemove} disabled={busy}>
                {remove.isPending ? t("cards.deleting") : t("cards.delete_yes")}
              </Button>
              <Button variant="secondary" onClick={() => setConfirmDelete(false)} disabled={busy}>
                {t("cards.cancel")}
              </Button>
            </div>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={busy}>
            {create.isPending || update.isPending ? t("cards.saving") : t("cards.save")}
          </Button>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {t("cards.cancel")}
          </Button>
          {editing && card?.editable && !confirmDelete ? (
            <Button variant="secondary" onClick={() => setConfirmDelete(true)} disabled={busy} className="ml-auto">
              {t("cards.delete")}
            </Button>
          ) : null}
        </div>
      </form>
    </dialog>
  );
}
