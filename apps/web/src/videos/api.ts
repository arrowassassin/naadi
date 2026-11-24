import {
  getVideosOverview,
  listVideos,
  listVideoTopics,
  markVideoWatched,
  unmarkVideoWatched,
  type VideoOut,
  type VideosOut,
  type VideosOverview,
  type VideoTopicsOut,
} from "@naadi/shared";
import {
  type InfiniteData,
  keepPreviousData,
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";

export type Step = "step1" | "step2ck";
export type Style = VideoOut["style"];

/** What the list shows; each field maps 1:1 to a `listVideos` query parameter. */
export interface VideoFilters {
  step?: Step;
  subject?: string;
  q?: string;
  channel?: string;
  style?: Style;
  core?: boolean;
  unwatched?: boolean;
}

const PAGE = 48;
/** The whole catalogue of one subject, for "More on this topic" (Pathology, the largest, is ~400). */
const SUBJECT_PAGE = 200;
const SUBJECT_MAX_PAGES = 10;
const STALE_MS = 60_000;

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** Retry once on a network blip; never on an answer from the server (rate limit, unavailable). */
function retry(count: number, err: unknown): boolean {
  return count < 1 && err instanceof ApiError && err.status === null;
}

function query(f: VideoFilters) {
  return {
    step: f.step ?? "all",
    subject: f.subject ?? null,
    q: f.q ?? null,
    channel: f.channel ?? null,
    style: f.style ?? null,
    core: f.core ?? false,
    unwatched: f.unwatched ?? false,
  } as const;
}

export function useVideoList(f: VideoFilters) {
  const userId = useUserId();
  return useInfiniteQuery({
    queryKey: ["videos", "list", userId, query(f)],
    enabled: userId !== null,
    placeholderData: keepPreviousData,
    staleTime: STALE_MS,
    retry,
    initialPageParam: null as string | null,
    getNextPageParam: (last: VideosOut) => last.next,
    queryFn: ({ pageParam, signal }) =>
      authed<VideosOut>((headers) =>
        listVideos({ signal, headers, query: { ...query(f), limit: PAGE, cursor: pageParam } }),
      ),
  });
}

/** Every video of one step + subject (a few pages at most), for the related lists of the player pane. */
export function useSubjectVideos(step: Step | null, subject: string | null) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["videos", "subject", userId, step, subject],
    enabled: userId !== null && step !== null && subject !== null,
    staleTime: 5 * STALE_MS,
    retry,
    queryFn: async ({ signal }): Promise<VideosOut> => {
      const items: VideoOut[] = [];
      let cursor: string | null = null;
      for (let i = 0; i < SUBJECT_MAX_PAGES; i++) {
        const page: VideosOut = await authed<VideosOut>((headers) =>
          listVideos({ signal, headers, query: { step: step ?? "all", subject, limit: SUBJECT_PAGE, cursor } }),
        );
        items.push(...page.items);
        cursor = page.next;
        if (!cursor) break;
      }
      return { items, next: null, total: items.length };
    },
  });
}

export function useVideosOverview() {
  const userId = useUserId();
  return useQuery({
    queryKey: ["videos", "overview", userId],
    enabled: userId !== null,
    staleTime: STALE_MS,
    retry,
    queryFn: ({ signal }) => authed<VideosOverview>((headers) => getVideosOverview({ signal, headers })),
  });
}

export function useVideoTopics(step: Step | undefined, subject: string | undefined, enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["videos", "topics", userId, step ?? null, subject ?? null],
    enabled: enabled && userId !== null,
    placeholderData: keepPreviousData,
    staleTime: 5 * STALE_MS,
    retry,
    queryFn: ({ signal }) =>
      authed<VideoTopicsOut>((headers) =>
        listVideoTopics({ signal, headers, query: { step: step ?? null, subject: subject ?? null } }),
      ),
  });
}

export interface WatchedChange {
  video: Pick<VideoOut, "media_id" | "step" | "subject">;
  watched: boolean;
}

type Cached = InfiniteData<VideosOut> | VideosOut | VideosOverview | VideoTopicsOut | undefined;

function patchItems(items: VideoOut[], id: string, watched: boolean): VideoOut[] {
  return items.map((v) => (v.media_id === id && v.watched !== watched ? { ...v, watched } : v));
}

/** One cached `["videos", ...]` value with the video's watched state (and the overview's count) changed. */
export function patchWatched(data: Cached, { video, watched }: WatchedChange): Cached {
  if (!data) return data;
  if ("pages" in data) {
    return { ...data, pages: data.pages.map((p) => ({ ...p, items: patchItems(p.items, video.media_id, watched) })) };
  }
  if ("subjects" in data) {
    const d = watched ? 1 : -1;
    return {
      ...data,
      subjects: data.subjects.map((s) =>
        s.step === video.step && s.subject === video.subject
          ? { ...s, watched: Math.min(s.videos, Math.max(0, s.watched + d)) }
          : s,
      ),
    };
  }
  if (data.items.length && "media_id" in (data.items[0] as object)) {
    const out = data as VideosOut;
    return { ...out, items: patchItems(out.items, video.media_id, watched) };
  }
  return data;
}

/** Every cached value that holds watched state: lists, subject catalogues and the overview (topics hold none). */
const WATCHED_QUERIES = {
  queryKey: ["videos"],
  predicate: (q: { queryKey: readonly unknown[] }) => q.queryKey[1] !== "topics",
};
const WATCHED_KEY = ["videos", "watched"];

function applyEverywhere(qc: QueryClient, change: WatchedChange): void {
  qc.setQueriesData<Cached>(WATCHED_QUERIES, (d) => patchWatched(d, change));
}

/**
 * Tick a video off (or back on) at once in every cached list, then tell the server.
 * - Requests run one at a time (one mutation scope), in tap order, so mark-then-unmark ends as the student left it.
 * - Fetches in flight are cancelled first, so none lands on top of the change with the old state.
 * - A failure applies the inverse change (not a snapshot: a snapshot would also undo a later toggle still queued).
 * - Once the last queued toggle settles, every list is refetched: the server's state wins, and a "Not watched yet"
 *   list drops what was just marked.
 */
export function useToggleWatched() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: WATCHED_KEY,
    scope: { id: "videos-watched" },
    mutationFn: ({ video, watched }: WatchedChange) =>
      authed<unknown>((headers) =>
        (watched ? markVideoWatched : unmarkVideoWatched)({ headers, path: { media_id: video.media_id } }),
      ),
    onMutate: async (change) => {
      await qc.cancelQueries(WATCHED_QUERIES);
      applyEverywhere(qc, change);
    },
    onError: (_err, change) => applyEverywhere(qc, { ...change, watched: !change.watched }),
    // This mutation still counts as running here: 1 = no other toggle is queued behind it.
    onSettled: () =>
      qc.isMutating({ mutationKey: WATCHED_KEY }) <= 1 ? qc.invalidateQueries(WATCHED_QUERIES) : undefined,
  });
}
