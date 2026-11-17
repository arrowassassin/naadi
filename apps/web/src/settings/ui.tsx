import { cx } from "@naadi/ui";
import { Check, ExternalLink as ExternalLinkIcon } from "lucide-react";

const PILL =
  "inline-flex min-h-11 items-center gap-2 rounded-pill px-4 text-ui transition-colors duration-(--duration-control) ease-out";

/** A pressed/unpressed pill (aria-pressed); the check icon and aria-pressed carry the state, not colour alone. */
export function Toggle({ on, onToggle, children }: { on: boolean; onToggle: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onToggle}
      className={cx(
        PILL,
        on
          ? "border border-transparent bg-paper font-semibold text-ground"
          : "border border-line-control bg-transparent text-text hover:border-text-3",
      )}
    >
      {on ? <Check aria-hidden="true" size={16} /> : null}
      {children}
    </button>
  );
}

export const FIELD =
  "min-h-14 w-full rounded-swatch border border-line-control bg-ground px-4 text-[1.0625rem] text-text aria-invalid:border-amber";

export const LINK = "text-text underline underline-offset-4";

/** An external link: opens in a new tab, says so to screen readers, shows the external-link icon. */
export function ExternalLink({ href, children, newTab }: { href: string; children: string; newTab: string }) {
  return (
    <a
      className={`${LINK} inline-flex min-h-11 items-center gap-1.5`}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
      <ExternalLinkIcon aria-hidden="true" size={14} className="shrink-0" />
      <span className="sr-only">{newTab}</span>
    </a>
  );
}
