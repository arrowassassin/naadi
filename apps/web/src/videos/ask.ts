import { askAssistant } from "../assistant/ask";
import type { PageContextRegistration } from "../assistant/pageContext";
import { currentVideoPosition } from "../video/playback";

export interface AskableVideo {
  media_id: string;
  title: string;
}

/** The page context of the Videos screen: the open video, its position read at send time. */
export function videoPageContext(video: AskableVideo | null, pageTitle: string): PageContextRegistration {
  return video
    ? { type: "video", id: video.media_id, title: video.title, readVideo: currentVideoPosition }
    : { type: "page", title: pageTitle };
}

/** Open the assistant with the video attached as a chip and a prefilled, unsent question about it. */
export function askAboutVideo(video: AskableVideo, draft: string): void {
  const now = currentVideoPosition();
  askAssistant({
    kind: "ask",
    context: { type: "video", id: video.media_id, ...(now?.media_id === video.media_id ? { t: now.t } : {}) },
    draft,
  });
}
