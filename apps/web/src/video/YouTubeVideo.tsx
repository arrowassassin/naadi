import type { MediaOut } from "@naadi/shared";
import { cx } from "@naadi/ui";
import { Component, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import type { ContentEnv } from "../content/types";
import { prefetchChunks } from "../notes/prefetch";
import { lazyWithFallback } from "../shell/lazyPage";
import { playerUrl, SANDBOX_ATTR } from "./bridge";
import { type BridgePlayer, connectPlayer, STATE_PLAYING } from "./bridgePlayer";
import { Facade } from "./Facade";
import { formatClock } from "./format";
import { clearVideoPosition, publishVideoPosition } from "./playback";
import { playerOrigin } from "./playerConfig";

/** The timestamped notes (the notes API and its offline queue) load beside the player, never with the chapter. */
const loadVideoNotes = () => import("./VideoNotes").then((m) => m.VideoNotes);
const VideoNotes = lazyWithFallback(loadVideoNotes, { fallback: null, unavailable: "inline" });

const DEFAULT_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];

/** The notes panel needs the app's providers; outside them (a preview, a bare test) it is simply absent. */
class QuietBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? null : this.props.children;
  }
}

export interface Chapter {
  startS: number;
  endS: number | null;
}

interface Props {
  media: MediaOut & { youtube_id: string };
  startS: number | null;
  endS: number | null;
  chapters: Chapter[];
  env: ContentEnv;
  title: string;
}

/**
 * A YouTube video through the official IFrame Player API on youtube-nocookie.com, unmodified and with no overlay.
 * Until the student presses play nothing is requested from YouTube; one press adds the iframe, loads the API and
 * plays once the player reports ready.
 */
export function YouTubeVideo({ media, startS, endS, chapters, env, title }: Props) {
  const t = env.text;
  const [loaded, setLoaded] = useState(false);
  // The notes panel is a separate chunk: fetched at rest, so it opens even if the student goes offline first.
  useEffect(() => prefetchChunks(loadVideoNotes), []);
  const [apiFailed, setApiFailed] = useState(false);
  const [playerError, setPlayerError] = useState(false);
  const [ready, setReady] = useState(false);
  const [rates, setRates] = useState<number[]>(DEFAULT_RATES);
  const [rate, setRate] = useState(1);
  const [now, setNow] = useState(startS ?? 0);
  const [range, setRange] = useState<{ start: number | null; end: number | null }>({ start: startS, end: endS });
  const frameRef = useRef<HTMLIFrameElement>(null);
  const playerRef = useRef<BridgePlayer | null>(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  const time = useCallback((): number => {
    const p = playerRef.current;
    return Math.floor(p ? p.currentTime() : now);
  }, [now]);

  useEffect(() => {
    if (!loaded) return;
    const frame = frameRef.current;
    if (!frame) return;
    let live = true;
    const player = connectPlayer(
      frame,
      playerOrigin(),
      { videoId: media.youtube_id, start: startS },
      {
        onReady: (r, current) => {
          if (!live) return;
          playerRef.current = player;
          setReady(true);
          if (r.length) setRates(r);
          setRate(current);
        },
        onState: () => undefined,
        onTime: (s, state) => {
          if (!live) return;
          const end = rangeRef.current.end;
          if (end !== null && s >= end && state === STATE_PLAYING) player.pause();
          setNow(s);
          publishVideoPosition(media.id, s);
        },
        onError: () => live && setPlayerError(true),
        onApiFailed: () => live && setApiFailed(true),
      },
    );
    return () => {
      live = false;
      clearVideoPosition(media.id);
      player.destroy();
      playerRef.current = null;
      setReady(false);
    };
  }, [loaded, media.id, media.youtube_id, startS]);

  const seek = useCallback((seconds: number, to?: { start: number | null; end: number | null }) => {
    const p = playerRef.current;
    if (to) setRange(to);
    if (!p) return;
    p.seek(seconds);
    p.play();
    setNow(seconds);
  }, []);

  function setSpeed(r: number) {
    playerRef.current?.setRate(r);
    setRate(r);
  }

  const watchUrl = `https://www.youtube.com/watch?v=${encodeURIComponent(media.youtube_id)}`;
  const controls = ready && !apiFailed && !playerError;
  const sorted = [...chapters].sort((a, b) => a.startS - b.startS);
  const active = [...sorted].reverse().find((c) => now >= c.startS);
  const btn = "inline-flex min-h-11 min-w-11 items-center justify-center rounded-pill border px-4 text-ui-sm";

  return (
    <div className="flex flex-col gap-4">
      <div className="relative aspect-video w-full overflow-hidden rounded-block border border-line bg-surface-2">
        {loaded ? (
          <iframe
            ref={frameRef}
            title={title}
            // The official player runs on a page of its own origin (deploy/player-space), never on the app's: the
            // third-party script cannot reach the signed-in session. See bridge.ts.
            src={playerUrl(playerOrigin(), window.location.origin)}
            sandbox={SANDBOX_ATTR}
            referrerPolicy="strict-origin-when-cross-origin"
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            className="absolute inset-0 size-full border-0"
          />
        ) : (
          <Facade title={title} duration={media.duration_s} env={env} onPlay={() => setLoaded(true)} />
        )}
      </div>

      {playerError ? (
        <p role="alert" className="m-0 flex flex-wrap items-center gap-3 text-ui-sm text-text-2">
          <span>{t("video.player_error")}</span>
          <a
            className="text-text underline underline-offset-4"
            href={watchUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t("video.watch_on_youtube")}
          </a>
        </p>
      ) : null}

      {controls ? (
        <fieldset className="m-0 flex min-w-0 flex-wrap items-center gap-2 border-0 p-0">
          <legend className="sr-only">{t("video.speed_label")}</legend>
          <span aria-hidden="true" className="font-mono text-label-sm text-text-2 uppercase">
            {t("video.speed")}
          </span>
          {rates.map((r) => (
            <button
              key={r}
              type="button"
              aria-pressed={rate === r}
              onClick={() => setSpeed(r)}
              className={cx(
                btn,
                rate === r ? "border-paper bg-paper font-semibold text-ground" : "border-line-control text-text",
              )}
            >
              {t("video.speed_option", { rate: String(r) })}
            </button>
          ))}
        </fieldset>
      ) : null}

      {sorted.length > 0 ? (
        <nav aria-label={t("video.chapters")} className="flex flex-col gap-2">
          <h3 className="m-0 font-semibold text-ui">{t("video.chapters")}</h3>
          <ol className="m-0 flex list-none flex-col gap-2 p-0">
            {sorted.map((c, i) => (
              <li key={`${c.startS}-${c.endS ?? "x"}`}>
                <button
                  type="button"
                  disabled={!controls}
                  aria-current={active === c ? "true" : undefined}
                  onClick={() => seek(c.startS, { start: c.startS, end: c.endS })}
                  className={cx(
                    "flex min-h-11 w-full items-center gap-3 rounded-block border px-3 py-2 text-left text-text disabled:opacity-60",
                    active === c ? "border-paper bg-surface-2" : "border-line bg-surface-1",
                  )}
                >
                  <span className="font-mono text-label-sm text-text-2">{formatClock(c.startS)}</span>
                  <span className="text-ui-sm">{t("video.chapter_untitled", { n: String(i + 1) })}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}

      {controls ? (
        <QuietBoundary>
          <VideoNotes mediaId={media.id} getTime={time} seek={(s) => seek(s)} />
        </QuietBoundary>
      ) : null}

      <p className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-label-sm text-text-3">
        <span>{t("video.source")}</span>
        <a
          className="text-text-2 underline underline-offset-4"
          href={t("video.terms_url")}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("video.terms")}
        </a>
      </p>
    </div>
  );
}
