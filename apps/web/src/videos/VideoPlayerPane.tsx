import type { MediaOut, VideoOut } from "@naadi/shared";
import { Button, Chip, Skeleton } from "@naadi/ui";
import { Check, Sparkles, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useAssistantAvailable } from "../assistant/ask";
import type { ContentEnv } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { Sheet } from "../learn/Sheet";
import { YouTubeVideo } from "../video/YouTubeVideo";
import { askAboutVideo } from "./ask";
import { FileVideo } from "./FileVideo";
import { Thumb, videoMeta, videoTitle } from "./VideoCard";

const RELATED_MAX = 6;

/**
 * Other videos of the same subject: first those that teach one of this video's own topics, then those of the
 * same chapter (a looser match, listed apart).
 */
export function relatedVideos(v: VideoOut, all: readonly VideoOut[]): { topic: VideoOut[]; chapter: VideoOut[] } {
  const topics = new Set(v.topics.map((x) => x.id));
  const chapters = new Set(v.topics.map((x) => x.chapter).filter((c): c is string => Boolean(c)));
  const others = all.filter((o) => o.media_id !== v.media_id);
  const topic = others.filter((o) => o.topics.some((x) => topics.has(x.id)));
  const seen = new Set(topic.map((o) => o.media_id));
  const chapter = others.filter(
    (o) => !seen.has(o.media_id) && o.topics.some((x) => x.chapter !== null && chapters.has(x.chapter)),
  );
  return { topic: topic.slice(0, RELATED_MAX), chapter: chapter.slice(0, RELATED_MAX) };
}

function Player({ v }: { v: VideoOut }) {
  const t = useUiText();
  const title = videoTitle(t, v);
  if (v.provider === "commons" && v.file_url) {
    return (
      <FileVideo
        mediaId={v.media_id}
        src={v.file_url}
        title={title}
        durationS={v.duration_s}
        attribution={v.attribution}
        licence={v.licence}
        pageUrl={v.page_url}
      />
    );
  }
  if (!v.youtube_id) return <p className="m-0 text-text-2">{t("content.video_missing")}</p>;
  const media: MediaOut & { youtube_id: string } = {
    id: v.media_id,
    kind: "video_youtube",
    youtube_id: v.youtube_id,
    title,
    caption: null,
    attribution: v.attribution,
    duration_s: v.duration_s,
    embeddable: true,
    height: null,
    width: null,
    source: null,
    url: null,
  };
  // The player reads only its wording from the content env (the rest is for chapter documents).
  const env: ContentEnv = {
    media: { [v.media_id]: media },
    questions: {},
    mode: "prod",
    sectionId: `videos-${v.media_id}`,
    headingBase: 3,
    minHeading: 3,
    embedOrigin: window.location.origin,
    text: t,
  };
  return <YouTubeVideo media={media} startS={null} endS={null} chapters={[]} env={env} title={title} />;
}

function RelatedList({ label, items, onOpen }: { label: string; items: VideoOut[]; onOpen: (v: VideoOut) => void }) {
  const t = useUiText();
  if (!items.length) return null;
  return (
    <section aria-label={label} className="flex flex-col gap-2">
      <h3 className="m-0 font-mono font-medium text-label text-text-3 uppercase tracking-[0.14em]">{label}</h3>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {items.map((o) => (
          <li key={o.media_id}>
            <button
              type="button"
              onClick={() => onOpen(o)}
              className="grid min-h-11 w-full grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-3 rounded-block-sm p-1 text-left text-text hover:bg-surface-2"
            >
              <Thumb v={o} small className="rounded-inner" />
              <span className="flex min-w-0 flex-col">
                <span className="line-clamp-2 font-semibold text-ui-sm">{videoTitle(t, o)}</span>
                <span className="truncate text-label-sm text-text-3">
                  {videoMeta(t, o)}
                  {o.watched ? ` · ${t("videos.watched")}` : ""}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PaneBody({
  v,
  related,
  failed,
  onToggle,
  onOpen,
}: {
  v: VideoOut;
  related: { topic: VideoOut[]; chapter: VideoOut[] } | null;
  /** The last watched toggle failed: said here, since the page behind a modal sheet is out of reach. */
  failed: boolean;
  onToggle: (v: VideoOut) => void;
  onOpen: (v: VideoOut) => void;
}) {
  const t = useUiText();
  const canAsk = useAssistantAvailable();
  const title = videoTitle(t, v);
  return (
    <div className="flex flex-col gap-5">
      <Player key={v.media_id} v={v} />
      <div className="flex flex-wrap gap-3">
        {/* The one lime action of the pane: tick it off. Once ticked, undoing it is an ordinary button. */}
        <Button
          variant={v.watched ? "secondary" : "primary"}
          icon={v.watched ? <Check size={16} /> : undefined}
          onClick={() => onToggle(v)}
        >
          {t(v.watched ? "videos.pane_watched" : "videos.pane_mark")}
        </Button>
        {canAsk ? (
          <Button
            variant="assistant"
            icon={<Sparkles size={16} />}
            onClick={() => askAboutVideo(t("videos.ask_draft", { title }))}
          >
            {t("videos.ask")}
          </Button>
        ) : null}
      </div>
      {failed ? (
        <p role="alert" className="m-0 text-amber text-ui">
          {t("videos.toggle_failed")}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-text-2 text-ui-sm">{videoMeta(t, v)}</span>
        <Chip tone="meta" srPrefix={t("videos.style_label")}>
          {t(`videos.style.${v.style}`)}
        </Chip>
        {v.core ? <Chip tone="meta">{t("videos.core")}</Chip> : null}
        {v.watched ? (
          <Chip tone="done" icon={<Check size={12} />}>
            {t("videos.watched")}
          </Chip>
        ) : null}
      </div>
      {v.topics.length ? (
        <section aria-label={t("videos.topics_label")} className="flex flex-col gap-2">
          <h3 className="m-0 font-mono font-medium text-label text-text-3 uppercase tracking-[0.14em]">
            {t("videos.topics_label")}
          </h3>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {v.topics.map((x) => (
              <li key={x.id}>
                <Chip tone="current">{x.title}</Chip>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {related === null ? (
        <Skeleton className="h-24 w-full rounded-block" />
      ) : related.topic.length || related.chapter.length ? (
        <div className="flex flex-col gap-5">
          <RelatedList label={t("videos.more_topic")} items={related.topic} onOpen={onOpen} />
          <RelatedList label={t("videos.more_chapter")} items={related.chapter} onOpen={onOpen} />
        </div>
      ) : null}
    </div>
  );
}

export interface PaneProps {
  /** The open video, or null when closed. `missing` = the URL names a video the list does not hold. */
  video: VideoOut | null;
  missing: boolean;
  layout: "side" | "sheet";
  related: { topic: VideoOut[]; chapter: VideoOut[] } | null;
  failed: boolean;
  onClose: () => void;
  onToggle: (v: VideoOut) => void;
  onOpen: (v: VideoOut) => void;
}

/** The player: a side pane beside the list (laptop, iPad landscape) or a sheet over it (portrait, phone). */
export function VideoPlayerPane({ video, missing, layout, related, failed, onClose, onToggle, onOpen }: PaneProps) {
  const t = useUiText();
  const closeRef = useRef<HTMLButtonElement>(null);
  const paneRef = useRef<HTMLElement>(null);
  const id = video?.media_id ?? null;
  useEffect(() => {
    if (!id || layout !== "side") return;
    // Opened from far down the list (or a shared link): bring the pane up beside it; it stays there (sticky).
    const pane = paneRef.current;
    if (pane && pane.getBoundingClientRect().top > window.innerHeight / 3) pane.scrollIntoView?.({ block: "start" });
    closeRef.current?.focus({ preventScroll: true });
  }, [id, layout]);

  const open = video !== null || missing;
  const heading = video ? videoTitle(t, video) : t("videos.missing_title");
  const body = video ? (
    <PaneBody v={video} related={related} failed={failed} onToggle={onToggle} onOpen={onOpen} />
  ) : (
    <p className="m-0 text-text-2">{t("videos.missing_body")}</p>
  );

  if (layout === "sheet") {
    return (
      <Sheet
        open={open}
        onClose={onClose}
        title={heading}
        closeLabel={t("videos.close")}
        className="max-h-[94dvh] tablet:max-h-[92dvh] tablet:w-[min(46rem,94vw)]"
      >
        {body}
      </Sheet>
    );
  }
  if (!open) return null;
  return (
    <aside
      ref={paneRef}
      aria-label={heading}
      data-pane=""
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      // below the sticky navigation, above the assistant dock
      className="sticky top-24 flex max-h-[calc(100dvh-13rem)] scroll-mt-24 flex-col overflow-hidden rounded-tile border border-line bg-surface-1"
    >
      <div className="flex items-start gap-3 border-line-soft border-b px-5 py-4">
        <h2 className="m-0 min-w-0 flex-1 font-bold font-display text-[1.375rem] leading-tight tracking-[-0.02em] [overflow-wrap:anywhere]">
          {heading}
        </h2>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label={t("videos.close")}
          className="flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control bg-surface-2"
        >
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">{body}</div>
    </aside>
  );
}
