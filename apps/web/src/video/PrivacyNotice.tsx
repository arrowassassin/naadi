import { Tile } from "@naadi/ui";
import { useId } from "react";
import { useUiText } from "../copy/useUiText";

/** The YouTube API Services disclosure (Developer Policies III.A): mount it in Settings -> About & privacy. */
export function YouTubePrivacyNotice() {
  const t = useUiText();
  const id = useId();
  const link = "text-text underline underline-offset-4";
  return (
    <Tile as="section" aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="m-0 font-display font-bold text-[1.5rem]">
        {t("privacy.youtube_title")}
      </h2>
      <p className="m-0 text-reading text-text-2">{t("privacy.youtube_body")}</p>
      <p className="m-0 flex flex-wrap gap-x-6 gap-y-2">
        <a className={link} href={t("video.terms_url")} target="_blank" rel="noopener noreferrer">
          {t("video.terms")}
        </a>
        <a className={link} href={t("privacy.youtube_google_url")} target="_blank" rel="noopener noreferrer">
          {t("privacy.youtube_google")}
        </a>
      </p>
      <p className="m-0 text-text-2 text-ui-sm">
        {t("privacy.youtube_revoke")}{" "}
        <a className={link} href={t("privacy.youtube_revoke_url")} target="_blank" rel="noopener noreferrer">
          {t("privacy.youtube_revoke_link")}
        </a>
      </p>
    </Tile>
  );
}
