import { Skeleton, Tile } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import { SETTINGS_GROUPS, type SettingsGroup, type SettingsGroupId, visibleGroups } from "./groups";
import { SettingsNav } from "./SettingsNav";

/** The frame of a Settings group: eyebrow, title, the group nav, then the group's tiles. */
export function SettingsPage({
  group,
  titleKey,
  headingId,
  children,
}: {
  group: SettingsGroupId;
  titleKey: string;
  headingId: string;
  children: ReactNode;
}) {
  const t = useUiText();
  const me = useMe();
  const meta: SettingsGroup | undefined = SETTINGS_GROUPS.find((g) => g.id === group);
  const flagged = meta?.flag;
  // The server switched this group off (or its flag is unknown): say so instead of showing a dead form.
  const off =
    meta !== undefined &&
    flagged !== undefined &&
    me.data !== undefined &&
    visibleGroups(me.data.features, undefined, [meta]).length === 0;
  const waiting = flagged !== undefined && me.isPending && me.data === undefined;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-8 pt-6 tablet:pt-12" data-settings-group={group}>
      <div className="flex flex-col gap-3">
        <span className="font-mono text-label text-text-3 uppercase">{t("account.settings_label")}</span>
        <h1 id={headingId} className="m-0 font-display text-headline">
          {t(titleKey)}
        </h1>
        <SettingsNav current={group} />
      </div>
      {off ? (
        <Tile as="section" className="flex flex-col gap-3" data-settings-off="">
          <p className="m-0 text-reading text-text-2">{t("settings.group_off")}</p>
          <p className="m-0">
            <Link
              to="/settings/account"
              className="inline-flex min-h-11 items-center text-text underline underline-offset-4"
            >
              {t("settings.group_off_link")}
            </Link>
          </p>
        </Tile>
      ) : waiting ? (
        <div aria-busy="true">
          <Skeleton className="h-48 rounded-tile" />
        </div>
      ) : (
        children
      )}
    </section>
  );
}
