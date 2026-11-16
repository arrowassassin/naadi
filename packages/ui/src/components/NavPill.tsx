import type { ReactNode } from "react";
import { cx } from "../cx";

export interface NavItem {
  id: string;
  label: string;
  /** Render as a link when set; otherwise a button that calls onSelect. */
  href?: string;
  icon?: ReactNode;
}

export interface NavPillProps {
  items: NavItem[];
  currentId: string;
  onSelect?: (id: string) => void;
  /** Accessible name of the navigation landmark. */
  label: string;
  className?: string;
}

/** Floating navigation pill for laptop and iPad (≥ 640 px). The current item is the paper-filled one. */
export function NavPill({ items, currentId, onSelect, label, className }: NavPillProps) {
  return (
    <nav aria-label={label} className={cx("w-fit max-w-full", className)}>
      <ul className="m-0 flex list-none items-center gap-1 overflow-x-auto rounded-pill border border-line bg-bar p-1.5">
        {items.map((item) => {
          const current = item.id === currentId;
          const cls = cx(
            "flex min-h-11 items-center gap-2 rounded-pill px-4 text-[0.875rem] whitespace-nowrap no-underline",
            "transition-colors duration-(--duration-control) ease-out",
            current
              ? "bg-paper font-semibold text-ground hover:text-ground"
              : "text-text-2 hover:bg-surface-2 hover:text-text",
          );
          const content = (
            <>
              {item.icon ? (
                <span aria-hidden="true" className="inline-flex">
                  {item.icon}
                </span>
              ) : null}
              {item.label}
            </>
          );
          return (
            <li key={item.id}>
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
        })}
      </ul>
    </nav>
  );
}
