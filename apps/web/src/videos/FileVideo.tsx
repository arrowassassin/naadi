import type { LicenceOut } from "@naadi/shared";
import { Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { formatClock } from "../video/format";
import { clearVideoPosition, publishVideoPosition } from "../video/playback";

export interface FileVideoProps {
  mediaId: string;
  /** The open-licence file (Wikimedia Commons); only https sources are played. */
  src: string;
  title: string;
  durationS: number | null;
  /** The credit line built from the licence when the video was imported. */
  attribution: string | null;
  licence: LicenceOut | null;
  /** The file's own page on its host. */
  pageUrl: string | null;
}

/** Whether this browser can decode the file; only WebM (what Commons serves us) is checked, anything else is tried. */
const canPlay = (u: string): boolean =>
  !/\.webm$/i.test(u.split(/[?#]/)[0] ?? "") ||
  document.createElement("video").canPlayType('video/webm; codecs="vp9, opus"') !== "";

const https = (u: string | null | undefined): string | null => (u && /^https:\/\//.test(u) ? u : null);

/**
 * An open-licence video file in the browser's own player. Click-to-load: nothing is fetched from the file's host
 * until the student presses play. Publishes where it is (the assistant's page context) while it plays.
 */
export function FileVideo({ mediaId, src, title, durationS, attribution, licence, pageUrl }: FileVideoProps) {
  const t = useUiText();
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLVideoElement>(null);
  const file = https(src);

  useEffect(() => {
    if (!loaded) return;
    // The press that loaded the file is the gesture that lets it play (iPad Safari).
    ref.current?.play()?.catch(() => undefined);
    return () => clearVideoPosition(mediaId);
  }, [loaded, mediaId]);

  const licenceUrl = https(licence?.license_url);
  const page = https(pageUrl);
  const unplayable = failed || (file !== null && !canPlay(file));
  const length = durationS && durationS > 0 ? formatClock(durationS) : null;

  return (
    <figure data-node="file-video" data-media-id={mediaId} className="m-0 flex flex-col gap-3">
      <div className="relative aspect-video w-full overflow-hidden rounded-block border border-line bg-surface-2">
        {!file ? (
          <p className="absolute inset-0 m-0 flex items-center justify-center px-6 text-center text-text-2">
            {t("content.video_missing")}
          </p>
        ) : unplayable ? (
          <p
            role="status"
            className="absolute inset-0 m-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-text-2"
          >
            {t("videos.file_unplayable")}
            {page ? (
              <a
                className="text-text underline underline-offset-4"
                href={page}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t("videos.file_page")}
              </a>
            ) : null}
          </p>
        ) : loaded ? (
          // biome-ignore lint/a11y/useMediaCaption: Commons files carry no separate caption track
          <video
            ref={ref}
            controls
            preload="none"
            playsInline
            src={file}
            onError={() => setFailed(true)}
            title={title}
            onTimeUpdate={(e) => publishVideoPosition(mediaId, e.currentTarget.currentTime)}
            className="absolute inset-0 size-full bg-ground"
          />
        ) : (
          <button
            type="button"
            onClick={() => setLoaded(true)}
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-text"
          >
            <span className="flex size-16 items-center justify-center rounded-pill bg-paper text-ground">
              <Play aria-hidden="true" size={26} />
            </span>
            <span className="px-6 text-center font-semibold">{t("content.video_play", { title })}</span>
            <span className="px-6 text-center text-text-2 text-ui-sm">
              {length ? t("videos.file_loads_with_length", { length }) : t("videos.file_loads")}
            </span>
          </button>
        )}
      </div>
      <figcaption className="flex flex-col gap-1 font-mono text-label-sm text-text-3">
        {attribution ? <span className="[overflow-wrap:anywhere]">{attribution}</span> : null}
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          {licence?.license_short_name ? (
            licenceUrl ? (
              <a
                className="text-text-2 underline underline-offset-4"
                href={licenceUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                {licence.license_short_name}
              </a>
            ) : (
              <span>{licence.license_short_name}</span>
            )
          ) : null}
          {page ? (
            <a
              className="text-text-2 underline underline-offset-4"
              href={page}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t("videos.file_page")}
            </a>
          ) : null}
        </span>
      </figcaption>
    </figure>
  );
}
