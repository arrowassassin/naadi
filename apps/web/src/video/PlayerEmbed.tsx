import { useEffect, useRef, useState } from "react";
import { playerUrl, SANDBOX_ATTR } from "./bridge";
import { connectPlayer } from "./bridgePlayer";
import { playerOrigin } from "./playerConfig";

/**
 * The official YouTube player for a plain video id, with no controls of its own: the same player page and bridge as the
 * lesson player (see bridge.ts), for places that only need to show a video (the agent diagnostics). Render it only
 * after the viewer has pressed a click-to-load facade: mounting it is what loads the player page.
 */
export function PlayerEmbed({
  videoId,
  title,
  errorText,
  watchText,
}: {
  videoId: string;
  title: string;
  /** Shown with a link to YouTube when the player reports an error or cannot load. */
  errorText: string;
  watchText: string;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const player = connectPlayer(
      frame,
      playerOrigin(),
      { videoId, start: null },
      {
        onReady: () => undefined,
        onState: () => undefined,
        onTime: () => undefined,
        onError: () => setFailed(true),
        onApiFailed: () => setFailed(true),
      },
    );
    return () => player.destroy();
  }, [videoId]);

  return (
    <>
      <iframe
        ref={frameRef}
        title={title}
        src={playerUrl(playerOrigin(), window.location.origin)}
        sandbox={SANDBOX_ATTR}
        referrerPolicy="strict-origin-when-cross-origin"
        allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
        allowFullScreen
        className="absolute inset-0 size-full border-0"
      />
      {failed ? (
        <p
          role="alert"
          className="absolute inset-x-0 bottom-0 m-0 flex flex-wrap items-center gap-2 bg-ground/90 p-3 text-text-2 text-ui-sm"
        >
          <span>{errorText}</span>
          <a
            className="text-text underline underline-offset-4"
            href={`https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {watchText}
          </a>
        </p>
      ) : null}
    </>
  );
}
