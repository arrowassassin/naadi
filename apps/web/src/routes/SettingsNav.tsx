import { cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { useUiText } from "../copy/useUiText";

const TAB =
  "inline-flex min-h-11 items-center rounded-pill border px-4 text-ui-sm no-underline transition-colors duration-(--duration-control) ease-out";

/** Settings sections (Settings board): Account · Pencil & ink. */
export function SettingsNav({ current }: { current: "account" | "ink" }) {
  const t = useUiText();
  const items = [
    { id: "account", to: "/settings/account", label: t("settings.nav_account") },
    { id: "ink", to: "/settings/ink", label: t("settings.nav_ink") },
  ] as const;
  return (
    <nav aria-label={t("settings.nav_label")}>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
        {items.map((i) => (
          <li key={i.id}>
            <Link
              to={i.to}
              aria-current={current === i.id ? "page" : undefined}
              className={cx(
                TAB,
                current === i.id
                  ? "border-paper bg-paper font-semibold text-ground hover:text-ground"
                  : "border-line-control text-text hover:bg-surface-2 hover:text-text",
              )}
            >
              {i.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
