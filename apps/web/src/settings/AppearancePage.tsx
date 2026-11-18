import { Chip, Tile } from "@naadi/ui";
import { Moon } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { SettingsPage } from "./SettingsPage";

function Row({ name, note, value }: { name: string; note: string; value?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-line-soft border-t py-4 first:border-t-0">
      <span className="flex min-w-0 flex-col gap-1">
        <span className="font-semibold text-[1.0625rem] text-text">{name}</span>
        <span className="text-reading text-text-2">{note}</span>
      </span>
      {value ? (
        <span className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-pill border border-line-control px-4 text-ui-sm whitespace-nowrap">
          {value}
        </span>
      ) : null}
    </div>
  );
}

/** Settings -> Appearance: the app is dark-only today, so this states facts and offers no control. */
export function AppearancePage() {
  const t = useUiText();
  return (
    <SettingsPage group="appearance" titleKey="settings.appearance_title" headingId="appearance-settings-h">
      <div className="grid items-start gap-5 laptop:grid-cols-2">
        <Tile as="section" aria-labelledby="theme-h" className="flex flex-col gap-2">
          <h2 id="theme-h" className="m-0 mb-2 font-display font-bold text-[1.5rem]">
            {t("settings.theme_heading")}
          </h2>
          <Row name={t("settings.theme_name")} note={t("settings.theme_note")} value={t("settings.theme_dark")} />
          <p className="m-0 flex items-center gap-2 text-text-3 text-ui-sm">
            <Moon aria-hidden="true" size={16} className="shrink-0" />
            {t("settings.theme_later")}
          </p>
        </Tile>
        <Tile as="section" aria-labelledby="motion-h" className="flex flex-col gap-2">
          <h2 id="motion-h" className="m-0 mb-2 font-display font-bold text-[1.5rem]">
            {t("settings.motion_heading")}
          </h2>
          <Row name={t("settings.motion_name")} note={t("settings.motion_note")} />
          <div>
            <Chip tone="meta">{t("settings.motion_chip")}</Chip>
          </div>
        </Tile>
      </div>
    </SettingsPage>
  );
}
