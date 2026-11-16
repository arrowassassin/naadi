import { ArrowUp, Plus } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { cx } from "../cx";

export interface AssistantBarProps {
  onSubmit: (text: string) => void | Promise<void>;
  onAttach?: () => void;
  /** Called when onSubmit rejects; the typed text is kept so nothing is lost. */
  onError?: (error: unknown) => void;
  /** The assistant is off (e.g. exam mode): the bar stays visible but inert, with the reason shown. */
  disabled?: boolean;
  disabledReason?: string;
  placeholder?: string;
  /** Accessible name of the form landmark. */
  label?: string;
  /** Accessible name of the text input. */
  inputLabel?: string;
  className?: string;
}

/** Assistant bar pinned to the bottom of every page: attach, input, send. All targets are 48 px. */
export function AssistantBar({
  onSubmit,
  onAttach,
  onError,
  disabled = false,
  disabledReason,
  placeholder = "Ask Naadi…",
  label = "Assistant",
  inputLabel = "Ask Naadi",
  className,
}: AssistantBarProps) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const inputId = useId();
  const reasonId = useId();
  const canSend = !disabled && !busy && text.trim().length > 0;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!canSend) return;
    setBusy(true);
    try {
      await onSubmit(text.trim());
      setText("");
    } catch (err) {
      if (onError) onError(err);
      else console.error("Assistant message could not be sent", err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label={label}
      className={cx(
        "flex items-center gap-2 rounded-pill border border-line-strong bg-bar p-1.5",
        disabled && "opacity-80",
        className,
      )}
    >
      <button
        type="button"
        onClick={onAttach}
        disabled={disabled || !onAttach}
        aria-label="Attach a file"
        className="flex size-12 shrink-0 items-center justify-center rounded-pill bg-control text-text hover:bg-line-strong disabled:cursor-not-allowed disabled:text-text-muted"
      >
        <Plus aria-hidden="true" size={20} strokeWidth={2} />
      </button>
      <label htmlFor={inputId} className="sr-only">
        {inputLabel}
      </label>
      <input
        id={inputId}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={disabled && disabledReason ? disabledReason : placeholder}
        disabled={disabled}
        aria-describedby={disabled && disabledReason ? reasonId : undefined}
        autoComplete="off"
        enterKeyHint="send"
        className="min-h-12 min-w-0 flex-1 bg-transparent px-1 text-[0.9375rem] text-text placeholder:text-text-3 focus-visible:outline-none disabled:cursor-not-allowed"
      />
      {disabled && disabledReason ? (
        <span id={reasonId} className="sr-only">
          {disabledReason}
        </span>
      ) : null}
      <button
        type="submit"
        disabled={!canSend}
        aria-label="Send"
        className="flex size-12 shrink-0 items-center justify-center rounded-pill bg-lime text-lime-ink hover:bg-lime-hover disabled:cursor-not-allowed disabled:bg-control disabled:text-text-muted"
      >
        <ArrowUp aria-hidden="true" size={20} strokeWidth={2.4} />
      </button>
    </form>
  );
}
