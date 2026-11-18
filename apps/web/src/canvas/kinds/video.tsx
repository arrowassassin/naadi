import { VideoBlock } from "../../assistant/blocks";
import { useCanvasTitle } from "../CanvasView";
import type { CanvasKindProps } from "../registry";
import type { CanvasVideoData } from "./index";

/** A YouTube video through the existing IFrame player node (click to load, privacy-enhanced domain). */
export default function VideoKind({ item }: CanvasKindProps<CanvasVideoData>) {
  const title = useCanvasTitle();
  return (
    <VideoBlock
      block={{
        id: item.key,
        message_id: null,
        type: "video",
        data: {
          provider: "youtube",
          video_id: item.data.video_id,
          media_id: item.data.media_id,
          title: title(item),
          start_s: item.data.start_s ?? null,
        },
      }}
    />
  );
}
