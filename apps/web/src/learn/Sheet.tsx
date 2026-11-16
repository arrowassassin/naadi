import { cx } from "@naadi/ui";
import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";
import { useUiText } from "../copy/useUiText";
import "./sheet.css";

/**
 * A modal sheet on the native <dialog> (focus trap, Esc, inert background, top layer): bottom sheet on
 * phone, centred panel from tablet up. 400 ms ease-out, off under reduced motion (tokens.css).
 */
export function Sheet({
  open,
  onClose,
  title,
  closeLabel,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Accessible name of the close button (ui_copy). */
  closeLabel?: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const t = useUiText();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: keyboard users close with Esc or the Close button
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // Esc is handled by <dialog> itself (onCancel); this only adds closing by a tap on the backdrop.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // backdrop tap
      }}
      className={cx(
        "mx-auto mt-auto mb-0 max-h-[85dvh] w-full max-w-none rounded-t-sheet-sm border border-line bg-surface-1 p-0 text-text",
        "tablet:m-auto tablet:max-h-[80dvh] tablet:w-[min(36rem,92vw)] tablet:rounded-sheet",
        "backdrop:bg-ground/80 open:animate-[naadi-sheet-in_var(--duration-sheet)_var(--ease-out)]",
        className,
      )}
    >
      {open ? (
        <div className="flex max-h-[inherit] flex-col">
          <div className="flex items-center gap-3 border-line-soft border-b px-5 py-4">
            <h2 id={titleId} className="m-0 flex-1 font-bold font-display text-[1.375rem] tracking-[-0.02em]">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label={closeLabel ?? t("learn.close")}
              className="flex size-11 items-center justify-center rounded-pill border border-line-control bg-surface-2"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-[max(20px,env(safe-area-inset-bottom))]">
            {children}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
