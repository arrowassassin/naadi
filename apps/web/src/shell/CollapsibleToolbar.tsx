import { ChevronDown, ChevronUp } from "lucide-react";
import { type ReactNode, useState } from "react";

export function CollapsibleToolbar({
  label,
  controls,
  className,
  children,
}: {
  label: string;
  controls?: string;
  className: string;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(true);
  const Chevron = expanded ? ChevronUp : ChevronDown;
  return (
    <div role="toolbar" aria-label={label} aria-controls={controls} className={className}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        title={label}
        className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-pill px-2 text-ui-sm"
      >
        <Chevron size={18} aria-hidden="true" />
        <span className={expanded ? "sr-only" : undefined}>{label}</span>
      </button>
      {expanded && children}
    </div>
  );
}
