import { cx } from "@naadi/ui";
import { Link, useRouter } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import { type SettingsGroup, type SettingsGroupId, visibleGroups } from "./groups";

const TAB =
  "inline-flex min-h-11 items-center whitespace-nowrap rounded-pill border px-4 text-ui-sm no-underline transition-colors duration-(--duration-control) ease-out";

/** Settings sections (Settings board). One horizontally scrollable pill row on phone, wrapping from tablet up. */
export function SettingsNav({ current, groups }: { current: SettingsGroupId; groups?: readonly SettingsGroup[] }) {
  const t = useUiText();
  const me = useMe();
  const router = useRouter();
  const routes = router.routesByPath as Record<string, unknown>;
  const items = visibleGroups(me.data?.features, routes, groups);
  const currentRef = useRef<HTMLAnchorElement>(null);
  // Phone: bring the current pill into view when the row scrolls.
  useEffect(() => {
    currentRef.current?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, []);
  return (
    <nav aria-label={t("settings.nav_label")} data-settings-nav="">
      <ul className="-mx-(--gutter) m-0 flex list-none flex-nowrap gap-2 overflow-x-auto px-(--gutter) py-1 tablet:mx-0 tablet:flex-wrap tablet:overflow-visible tablet:px-0">
        {items.map((i) => (
          <li key={i.id} className="shrink-0">
            <Link
              ref={current === i.id ? currentRef : undefined}
              to={i.to}
              aria-current={current === i.id ? "page" : undefined}
              className={cx(
                TAB,
                current === i.id
                  ? "border-paper bg-paper font-semibold text-ground hover:text-ground"
                  : "border-line-control text-text hover:bg-surface-2 hover:text-text",
              )}
            >
              {t(i.labelKey)}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
