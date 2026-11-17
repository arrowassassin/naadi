import type { MediaOut } from "@naadi/shared";
import { cx } from "@naadi/ui";
import { FileText, ImageOff, Maximize2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { YouTubeVideo } from "../video/YouTubeVideo";
import type { ContentEnv } from "./types";

/** Shown when a node points at media the caller cannot see or the server cannot serve. Never silent. */
export function MediaUnavailable({
  message,
  mediaId,
  env,
}: {
  message: string;
  mediaId: string | null;
  env: ContentEnv;
}) {
  return (
    <div
      data-node="media-unavailable"
      className="flex items-center gap-3 rounded-block border border-dashed border-line-dashed px-4 py-3 text-text-2"
    >
      <ImageOff aria-hidden="true" size={18} className="shrink-0" />
      <span>
        {message}
        {env.mode === "dev" && mediaId ? <span className="ml-1 font-mono text-label-sm">({mediaId})</span> : null}
      </span>
    </div>
  );
}

function credit(m: MediaOut): string | null {
  if (m.attribution) return m.attribution;
  if (m.source) return `${m.source.name} · ${m.source.license}`;
  return null;
}

function Caption({ m, env }: { m: MediaOut; env: ContentEnv }) {
  const by = credit(m);
  if (!m.caption && !by) return null;
  return (
    <figcaption className="flex flex-col gap-1 text-ui-sm text-text-2">
      {m.caption ? <span>{m.caption}</span> : null}
      {by ? (
        <span className="font-mono text-label-sm text-text-3">{env.text("content.source", { credit: by })}</span>
      ) : null}
    </figcaption>
  );
}

export function Figure({ mediaId, env }: { mediaId: string | null; env: ContentEnv }) {
  const m = mediaId ? env.media[mediaId] : undefined;
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const d = dialogRef.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);

  if (!m?.url || (m.kind !== "svg" && m.kind !== "image")) {
    return <MediaUnavailable message={env.text("content.figure_missing")} mediaId={mediaId} env={env} />;
  }
  const alt = m.caption ?? m.title ?? env.text("content.figure_fallback_alt");
  // Intrinsic size reserves the box before the lazy image loads (a restored scroll position must not jump).
  const w = m.width ?? 4;
  const h = m.height ?? 3;
  return (
    <figure data-node="figure" data-media-id={m.id} className="m-0 flex flex-col gap-3">
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className="group relative block w-full cursor-zoom-in rounded-block border border-line bg-white p-3 tablet:p-5"
      >
        <span className="sr-only">{env.text("content.figure_enlarge")}</span>
        <img
          src={m.url}
          alt={alt}
          width={w}
          height={h}
          loading="lazy"
          decoding="async"
          className="mx-auto block h-auto max-h-[75vh] w-full max-w-full object-contain"
        />
        <span
          aria-hidden="true"
          className="absolute top-3 right-3 flex size-9 items-center justify-center rounded-pill bg-ground/80 text-text opacity-90 group-hover:opacity-100"
        >
          <Maximize2 size={16} />
        </span>
      </button>
      <Caption m={m} env={env} />
      <dialog
        ref={dialogRef}
        aria-label={alt}
        onClose={() => setOpen(false)}
        onCancel={() => setOpen(false)}
        className="m-auto h-[min(92dvh,1200px)] w-[min(96vw,1600px)] max-w-none rounded-tile border border-line bg-ground p-0 text-text backdrop:bg-ground/85"
      >
        {open ? (
          <div className="flex h-full flex-col gap-3 p-3 tablet:p-5">
            <div className="flex items-start gap-3">
              <p className="m-0 min-w-0 flex-1 text-ui-sm text-text-2">{m.caption}</p>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label={env.text("content.figure_close")}
                className="flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control bg-surface-2"
              >
                <X aria-hidden="true" size={18} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto rounded-block bg-white p-2">
              <img src={m.url} alt={alt} width={w} height={h} className="mx-auto block h-full w-full object-contain" />
            </div>
          </div>
        ) : null}
      </dialog>
    </figure>
  );
}

export function Video({
  mediaId,
  startS,
  endS,
  env,
}: {
  mediaId: string | null;
  startS: number | null;
  endS: number | null;
  env: ContentEnv;
}) {
  const m = mediaId ? env.media[mediaId] : undefined;
  if (!m) return <MediaUnavailable message={env.text("content.video_missing")} mediaId={mediaId} env={env} />;
  const title = m.title ?? m.caption ?? env.text("content.video_fallback_title");

  if (m.kind === "video_youtube" && m.youtube_id && m.embeddable !== false) {
    const own = startS !== null ? [{ startS, endS }] : [];
    const chapters = [...own, ...(env.videoRanges?.[m.id] ?? []).filter((r) => r.startS !== startS)];
    return (
      <figure data-node="video" data-media-id={m.id} className="m-0 flex flex-col gap-3">
        <YouTubeVideo
          media={{ ...m, youtube_id: m.youtube_id }}
          startS={startS}
          endS={endS}
          chapters={chapters}
          env={env}
          title={title}
        />
        <Caption m={m} env={env} />
      </figure>
    );
  }
  if (m.kind === "video_own" && m.url) {
    return (
      <figure data-node="video" data-media-id={m.id} className="m-0 flex flex-col gap-3">
        {/* biome-ignore lint/a11y/useMediaCaption: own videos carry their caption track in the file when one exists */}
        <video
          controls
          preload="metadata"
          playsInline
          src={m.url}
          title={title}
          className="aspect-video w-full rounded-block border border-line bg-surface-2"
        />
        <Caption m={m} env={env} />
      </figure>
    );
  }
  return <MediaUnavailable message={env.text("content.video_missing")} mediaId={mediaId} env={env} />;
}

export function PdfLink({ mediaId, page, env }: { mediaId: string | null; page: number | null; env: ContentEnv }) {
  const m = mediaId ? env.media[mediaId] : undefined;
  if (!m?.url || m.kind !== "pdf")
    return <MediaUnavailable message={env.text("content.pdf_missing")} mediaId={mediaId} env={env} />;
  const href = page ? `${m.url}#page=${page}` : m.url;
  const label = m.title ?? m.caption ?? env.text("content.pdf_fallback_title");
  return (
    <div data-node="pdf" className="flex flex-col gap-2">
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={cx(
          "flex min-h-14 items-center gap-3 rounded-block border border-line bg-surface-2 px-4 py-3 text-text no-underline",
          "hover:border-text-3 hover:text-text",
        )}
      >
        <FileText aria-hidden="true" size={20} className="shrink-0 text-text-2" />
        <span className="flex min-w-0 flex-col">
          <span className="font-semibold">{label}</span>
          <span className="text-ui-sm text-text-2">
            {page ? env.text("content.pdf_open_page", { page: String(page) }) : env.text("content.pdf_open")}
          </span>
        </span>
      </a>
      {credit(m) ? (
        <span className="font-mono text-label-sm text-text-3">
          {env.text("content.source", { credit: credit(m) ?? "" })}
        </span>
      ) : null}
    </div>
  );
}
