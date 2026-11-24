import type { VideoOut } from "@naadi/shared";
import { Chip, cx } from "@naadi/ui";
import { Check, Play } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { formatClock } from "../video/format";

type T = ReturnType<typeof useUiText>;

export function videoTitle(t: T, v: VideoOut): string {
  return v.title ?? t("content.video_fallback_title");
}

/** "Dirty Medicine · 12:34", or the channel alone when the length is not known yet. */
export function videoMeta(t: T, v: VideoOut): string {
  const channel = v.channel_name ?? t(v.provider === "commons" ? "videos.channel_commons" : "video.source");
  return v.duration_s ? `${channel} · ${formatClock(v.duration_s)}` : channel;
}

/** YouTube's own still (i.ytimg.com, allowed by img-src) or a neutral poster for an open-licence file. */
export function Thumb({ v, className }: { v: VideoOut; className?: string }) {
  const t = useUiText();
  return (
    <span
      className={cx(
        "relative flex aspect-video w-full items-center justify-center overflow-hidden rounded-block-sm bg-surface-2",
        className,
      )}
    >
      {v.provider === "youtube" && v.youtube_id ? (
        <img
          src={`https://i.ytimg.com/vi/${encodeURIComponent(v.youtube_id)}/hqdefault.jpg`}
          alt=""
          width={480}
          height={360}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <span className="flex flex-col items-center gap-2 text-text-2">
          <Play aria-hidden="true" size={28} />
          <span className="font-mono text-label-sm uppercase tracking-[0.1em]">{t("videos.channel_commons")}</span>
        </span>
      )}
    </span>
  );
}

export function WatchedToggle({
  v,
  onToggle,
  className,
}: {
  v: VideoOut;
  onToggle: (v: VideoOut) => void;
  className?: string;
}) {
  const t = useUiText();
  const title = videoTitle(t, v);
  return (
    <button
      type="button"
      aria-pressed={v.watched}
      aria-label={t(v.watched ? "videos.watched_label" : "videos.mark_label", { title })}
      onClick={() => onToggle(v)}
      className={cx(
        "inline-flex min-h-11 items-center gap-1.5 rounded-pill px-4 text-ui-sm",
        v.watched ? "bg-surface-2 font-semibold text-text" : "border border-line-control text-text hover:bg-surface-2",
        className,
      )}
    >
      {v.watched ? <Check aria-hidden="true" size={16} /> : null}
      {t(v.watched ? "videos.watched" : "videos.mark")}
    </button>
  );
}

export function VideoCard({
  v,
  selected,
  onOpen,
  onToggle,
}: {
  v: VideoOut;
  selected: boolean;
  onOpen: (v: VideoOut) => void;
  onToggle: (v: VideoOut) => void;
}) {
  const t = useUiText();
  return (
    <li
      data-video={v.media_id}
      className={cx(
        "flex flex-col gap-3 rounded-tile-sm border bg-surface-1 p-3",
        selected ? "border-text" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => onOpen(v)}
        aria-current={selected ? "true" : undefined}
        className="flex flex-col gap-3 rounded-block-sm text-left text-text"
      >
        <Thumb v={v} />
        <span className="flex min-w-0 flex-col gap-0.5 px-1">
          <span className="line-clamp-2 font-semibold text-[1rem] leading-snug [overflow-wrap:anywhere]">
            {videoTitle(t, v)}
          </span>
          <span className="truncate text-text-3 text-ui-sm">{videoMeta(t, v)}</span>
        </span>
      </button>
      <span className="mt-auto flex flex-wrap items-center gap-2 px-1">
        <Chip tone="meta" srPrefix={t("videos.style_label")}>
          {t(`videos.style.${v.style}`)}
        </Chip>
        {v.core ? <Chip tone="meta">{t("videos.core")}</Chip> : null}
        <WatchedToggle v={v} onToggle={onToggle} className="ml-auto" />
      </span>
    </li>
  );
}
