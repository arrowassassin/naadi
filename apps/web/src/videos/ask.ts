import type { PageContextRegistration } from "../assistant/pageContext";
import { openAssistant, setAssistantUi } from "../assistant/store";
import { currentVideoPosition } from "../video/playback";

export interface AskableVideo {
  media_id: string;
  title: string;
}

/**
 * The page context of the Videos screen: the open video's position is read at send time. The assistant's page
 * types have no "video" yet (the server's do), so this is a plain page with the video position; switching it to
 * `{ type: "video", id: media_id }` here is the one change the assistant's video context needs.
 */
export function videoPageContext(video: AskableVideo | null, pageTitle: string): PageContextRegistration {
  return video
    ? { type: "page", id: video.media_id, title: video.title, readVideo: currentVideoPosition }
    : { type: "page", title: pageTitle };
}

/**
 * Open the assistant with a prefilled, unsent question about the video. `askAssistant` needs an ask context and its
 * union has no video kind yet, so the draft goes straight into the assistant's input; the video itself reaches the
 * server through the page context above.
 */
export function askAboutVideo(draft: string): void {
  setAssistantUi({ draft });
  openAssistant({ focus: true });
}
