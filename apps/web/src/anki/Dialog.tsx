import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

/** A modal <dialog> like the card dialog: closes on Escape (unless busy), focus returns to the opener. */
export function Modal({
  open,
  titleId,
  title,
  closeLabel,
  busy,
  onClose,
  children,
}: {
  open: boolean;
  titleId: string;
  title: string;
  closeLabel: string;
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(e) => {
        if (busy) e.preventDefault();
      }}
      className="m-auto max-h-[calc(100dvh-32px)] w-[min(100%-32px,640px)] overflow-y-auto rounded-tile-sm border border-line-strong bg-surface-1 p-0 text-text backdrop:bg-ground/70 backdrop:backdrop-blur-sm"
    >
      {open ? (
        <div className="flex flex-col gap-5 p-6 tablet:p-8">
          <div className="flex items-start justify-between gap-4">
            <h2 id={titleId} className="m-0 font-display text-lead">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              aria-label={closeLabel}
              className="flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control bg-surface-2"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </div>
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
