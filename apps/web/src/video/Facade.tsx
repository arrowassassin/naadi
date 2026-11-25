import { Play } from "lucide-react";
import type { ContentEnv } from "../content/types";

function duration(s: number | null, env: ContentEnv): string | null {
  if (s === null || s <= 0) return null;
  const m = String(Math.floor(s / 60));
  const r = String(s % 60);
  if (m === "0") return env.text("content.duration_s", { s: r });
  return env.text(r === "0" ? "content.duration_min" : "content.duration_min_s", { m, s: r });
}

/** Click-to-load: a plain button. No YouTube script or frame is requested before it is pressed. */
export function Facade({
  title,
  duration: seconds,
  env,
  onPlay,
}: {
  title: string;
  duration: number | null;
  env: ContentEnv;
  onPlay: () => void;
}) {
  const len = duration(seconds, env);
  return (
    <button
      type="button"
      onClick={onPlay}
      className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-text"
    >
      <span className="flex size-16 items-center justify-center rounded-pill bg-paper text-ground">
        <Play aria-hidden="true" size={26} />
      </span>
      <span className="px-6 text-center font-semibold">{env.text("content.video_play", { title })}</span>
      <span className="text-ui-sm text-text-2">
        {env.text(len ? "content.video_loads_with_length" : "content.video_loads", { length: len ?? "" })}
      </span>
    </button>
  );
}
