import { ArrowUp, Globe, Plus, Square } from "lucide-react";
import { type FormEvent, type ReactNode, type Ref, useId, useLayoutEffect, useRef, useState } from "react";
import { cx } from "../cx";

export interface AssistantBarWebToggle {
  on: boolean;
  onToggle: () => void;
  /** Accessible name, e.g. "Search the web"; the pressed state carries on/off. */
  label: string;
}

export interface AssistantBarProps {
  onSubmit: (text: string) => void | Promise<void>;
  onAttach?: () => void;
  /** Called when onSubmit rejects; the typed text is kept so nothing is lost. */
  onError?: (error: unknown) => void;
  /** The assistant is off (e.g. exam mode, offline): the bar stays visible but inert, with the reason shown. */
  disabled?: boolean;
  disabledReason?: string;
  placeholder?: string;
  /** Accessible name of the form landmark. */
  label?: string;
  /** Accessible name of the text input. */
  inputLabel?: string;
  attachLabel?: string;
  sendLabel?: string;
  /** Controlled text (e.g. a draft kept across a session refresh); uncontrolled when omitted. */
  value?: string;
  onValueChange?: (text: string) => void;
  /** The web toggle (Dock board); absent when not offered. */
  web?: AssistantBarWebToggle;
  /** While an answer streams: a Stop control replaces Send. */
  onStop?: () => void;
  stopLabel?: string;
  /** Sending is blocked (e.g. a confirm is waiting) while the input stays usable. */
  sendBlocked?: boolean;
  /** Extra content above the input row (attachment chips). */
  before?: ReactNode;
  maxLength?: number;
  inputRef?: Ref<HTMLTextAreaElement>;
  className?: string;
}

const ROUND = "flex size-12 shrink-0 items-center justify-center rounded-pill";

/**
 * Assistant bar (Dock board): attach, input, web toggle, send. All targets are 48 px. No voice button: iPadOS
 * dictation works in the field natively. The send button is the view's one lime action and only lights up once
 * there is something to send. Enter sends; Shift+Enter adds a line.
 */
export function AssistantBar({
  onSubmit,
  onAttach,
  onError,
  disabled = false,
  disabledReason,
  placeholder = "Ask Naadi…",
  label = "Assistant",
  inputLabel = "Ask Naadi",
  attachLabel = "Attach a file",
  sendLabel = "Send",
  value,
  onValueChange,
  web,
  onStop,
  stopLabel = "Stop",
  sendBlocked = false,
  before,
  maxLength,
  inputRef,
  className,
}: AssistantBarProps) {
  const [own, setOwn] = useState("");
  const text = value ?? own;
  const setText = (t: string) => {
    if (value === undefined) setOwn(t);
    onValueChange?.(t);
  };
  const [busy, setBusy] = useState(false);
  const ownRef = useRef<HTMLTextAreaElement | null>(null);
  // One line while empty (a long placeholder never grows the bar), then up to 160 px with the text.
  useLayoutEffect(() => {
    const el = ownRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = text ? `${Math.min(el.scrollHeight, 160)}px` : "48px";
  }, [text]);
  const setRefs = (el: HTMLTextAreaElement | null) => {
    ownRef.current = el;
    if (typeof inputRef === "function") inputRef(el);
    else if (inputRef) inputRef.current = el;
  };
  const inputId = useId();
  const reasonId = useId();
  const canSend = !disabled && !busy && !sendBlocked && text.trim().length > 0;

  async function send() {
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

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void send();
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label={label}
      className={cx(
        "flex flex-col gap-1.5 rounded-[28px] border border-line-strong bg-bar p-1.5",
        disabled && "opacity-80",
        className,
      )}
    >
      {before}
      <div className="flex items-end gap-2">
        <button
          type="button"
          onClick={onAttach}
          disabled={disabled || !onAttach}
          aria-label={attachLabel}
          className={cx(
            ROUND,
            "bg-control text-text hover:bg-line-strong disabled:cursor-not-allowed disabled:text-text-muted",
          )}
        >
          <Plus aria-hidden="true" size={20} strokeWidth={2} />
        </button>
        <label htmlFor={inputId} className="sr-only">
          {inputLabel}
        </label>
        <textarea
          id={inputId}
          ref={setRefs}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder={disabled && disabledReason ? disabledReason : placeholder}
          disabled={disabled}
          maxLength={maxLength}
          aria-describedby={disabled && disabledReason ? reasonId : undefined}
          autoComplete="off"
          enterKeyHint="send"
          className="min-h-12 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-1 py-3 text-[1rem] text-text leading-normal placeholder:truncate placeholder:text-text-3 focus-visible:outline-none disabled:cursor-not-allowed"
        />
        {disabled && disabledReason ? (
          <span id={reasonId} className="sr-only">
            {disabledReason}
          </span>
        ) : null}
        {web ? (
          <button
            type="button"
            onClick={web.onToggle}
            disabled={disabled}
            aria-pressed={web.on}
            aria-label={web.label}
            className={cx(
              ROUND,
              "border disabled:cursor-not-allowed disabled:text-text-muted",
              web.on
                ? "border-violet bg-violet-ground text-violet-text"
                : "border-transparent text-text-2 hover:bg-control",
            )}
          >
            <Globe aria-hidden="true" size={20} strokeWidth={2} />
          </button>
        ) : null}
        {onStop ? (
          <button
            type="button"
            onClick={onStop}
            aria-label={stopLabel}
            className={cx(ROUND, "border border-line-control bg-control text-text hover:bg-line-strong")}
          >
            <Square aria-hidden="true" size={16} strokeWidth={2.4} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSend}
            aria-label={sendLabel}
            className={cx(
              ROUND,
              "bg-lime text-lime-ink hover:bg-lime-hover disabled:cursor-not-allowed disabled:bg-control disabled:text-text-muted",
            )}
          >
            <ArrowUp aria-hidden="true" size={20} strokeWidth={2.4} />
          </button>
        )}
      </div>
    </form>
  );
}
