import { Tile, TileLabel } from "@naadi/ui";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { YouTubePrivacyNotice } from "../video/PrivacyNotice";
import { SettingsPage } from "./SettingsPage";

function Bullets({ id, items }: { id: string; items: string[] }) {
  return (
    <ul aria-labelledby={id} className="m-0 flex list-disc flex-col gap-3 pl-5 text-reading text-text-2">
      {items.map((i) => (
        <li key={i}>{i}</li>
      ))}
    </ul>
  );
}

/** Settings -> About & privacy (always shown): version, what is stored where, the AI statement, YouTube, sources. */
export function AboutPage() {
  const t = useUiText();
  const auth = useAuth();
  const version = auth.status === "signed-in" || auth.status === "signed-out" ? auth.config.app_version : null;
  const nums = [1, 2, 3, 4, 5];
  const ai = [1, 2, 3];
  return (
    <SettingsPage group="about" titleKey="settings.about_title" headingId="about-settings-h">
      <div className="grid items-start gap-5 laptop:grid-cols-[2fr_1fr]">
        <div className="flex flex-col gap-5">
          <Tile as="section" aria-labelledby="stores-h" className="flex flex-col gap-4">
            <h2 id="stores-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("settings.about_stores_heading")}
            </h2>
            <Bullets id="stores-h" items={nums.map((n) => t(`settings.about_stores_${n}`))} />
          </Tile>
          <Tile as="section" aria-labelledby="ai-h" className="flex flex-col gap-4">
            <h2 id="ai-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("settings.about_ai_heading")}
            </h2>
            <Bullets id="ai-h" items={ai.map((n) => t(`settings.about_ai_${n}`))} />
          </Tile>
          <Tile as="section" aria-labelledby="sources-h" className="flex flex-col gap-4">
            <h2 id="sources-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("settings.about_sources_heading")}
            </h2>
            <p className="m-0 text-reading text-text-2">{t("settings.about_sources_body")}</p>
          </Tile>
        </div>
        <div className="flex flex-col gap-5">
          <Tile as="section" aria-labelledby="version-h" className="flex flex-col gap-3">
            <TileLabel>{t("settings.about_version_label")}</TileLabel>
            <h2 id="version-h" className="m-0 font-display font-bold text-[1.5rem]" data-app-version="">
              {version ? t("settings.about_version", { version }) : t("settings.about_version_unknown")}
            </h2>
          </Tile>
          <YouTubePrivacyNotice />
        </div>
      </div>
    </SettingsPage>
  );
}
