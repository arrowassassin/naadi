import { cx } from "@naadi/ui";
import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";
import { useUiText } from "../copy/useUiText";

/**
 * A modal sheet on the native `<dialog>` (focus is trapped and Escape closes it by the platform): a bottom sheet on a
 * phone, a centred sheet on tablets and laptops. Used by the task sheet, the cost form, the vault form and the guide.
 */
export function Sheet({
  open,
  onClose,
  title,
  children,
  wide = false,
  handle,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  wide?: boolean;
  /** Test and automation handle (never shown). */
  handle: string;
}) {
  const t = useUiText();
  const ref = useRef<HTMLDialogElement>(null);
  const ids = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={`${ids}-h`}
      data-sheet={handle}
      onClose={onClose}
      className={cx(
        "m-auto max-h-[calc(100dvh-24px)] w-[min(100%-24px,var(--sheet-w))] overflow-y-auto rounded-sheet-sm border border-line-soft bg-surface-1 p-0 text-text backdrop:bg-ground/80",
        "max-tablet:mb-3 max-tablet:rounded-b-tile-sm",
        wide ? "[--sheet-w:52rem]" : "[--sheet-w:40rem]",
      )}
    >
      <div className="flex flex-col gap-5 p-[clamp(20px,4vw,32px)]">
        <div className="flex items-start justify-between gap-4">
          <h2
            id={`${ids}-h`}
            className="m-0 min-w-0 font-display font-bold text-[1.5rem] leading-tight tracking-[-0.02em]"
          >
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("pathway.close")}
            className="flex size-11 shrink-0 items-center justify-center rounded-full border border-line-control bg-transparent text-text"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        {open ? children : null}
      </div>
    </dialog>
  );
}
