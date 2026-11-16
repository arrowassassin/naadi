import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "../cx";
import type { NavItem } from "./NavPill";

export interface TabBarProps {
  /** Four tabs: the first two sit left of the assistant button, the rest to the right. */
  items: NavItem[];
  currentId: string;
  onSelect?: (id: string) => void;
  onAssistant: () => void;
  assistantLabel?: string;
  assistantIcon?: ReactNode;
  label: string;
  className?: string;
}

/** Phone (< 640 px) bottom tab bar with the centre assistant button (violet: the assistant's colour). */
export function TabBar({
  items,
  currentId,
  onSelect,
  onAssistant,
  assistantLabel = "Ask Naadi",
  assistantIcon,
  label,
  className,
}: TabBarProps) {
  const half = Math.ceil(items.length / 2);
  const renderTab = (item: NavItem) => {
    const current = item.id === currentId;
    const cls = cx(
      "flex min-h-12 min-w-11 flex-col items-center justify-center gap-0.5 rounded-block-sm px-2 text-[0.6875rem] no-underline",
      current ? "font-semibold text-text" : "text-text-3 hover:text-text",
    );
    const content = (
      <>
        {item.icon ? (
          <span aria-hidden="true" className="inline-flex">
            {item.icon}
          </span>
        ) : null}
        <span>{item.label}</span>
        <span
          aria-hidden="true"
          className={cx("mt-0.5 h-1 w-4 rounded-pill", current ? "bg-paper" : "bg-transparent")}
        />
      </>
    );
    return (
      <li key={item.id} className="flex justify-center">
        {item.href ? (
          <a href={item.href} aria-current={current ? "page" : undefined} className={cls}>
            {content}
          </a>
        ) : (
          <button
            type="button"
            aria-current={current ? "page" : undefined}
            className={cls}
            onClick={() => onSelect?.(item.id)}
          >
            {content}
          </button>
        )}
      </li>
    );
  };

  return (
    <nav aria-label={label} className={cx("w-full", className)}>
      <ul className="m-0 grid list-none grid-cols-5 items-center gap-1 rounded-tile-sm border border-line bg-bar p-1.5">
        {items.slice(0, half).map(renderTab)}
        <li className="flex justify-center">
          <button
            type="button"
            onClick={onAssistant}
            aria-label={assistantLabel}
            className="flex size-14 items-center justify-center rounded-pill bg-violet text-violet-ink transition-transform duration-(--duration-control) ease-out hover:bg-violet-text active:scale-95"
          >
            <span aria-hidden="true" className="inline-flex">
              {assistantIcon ?? <Sparkles size={22} strokeWidth={2.2} />}
            </span>
          </button>
        </li>
        {items.slice(half).map(renderTab)}
      </ul>
    </nav>
  );
}
