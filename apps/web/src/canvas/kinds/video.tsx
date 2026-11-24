import { VideoBlock } from "../../assistant/blocks";
import { useCanvasTitle } from "../CanvasView";
import type { CanvasKindProps } from "../registry";
import type { CanvasVideoData } from "./index";

/** A library video: YouTube through the official player node, an open-licence file in the browser's player. */
export default function VideoKind({ item }: CanvasKindProps<CanvasVideoData>) {
  const title = useCanvasTitle();
  return (
    <VideoBlock
      block={{
        id: item.key,
        message_id: null,
        type: "video",
        data: { ...item.data, title: title(item), start_s: item.data.start_s ?? null },
      }}
    />
  );
}
