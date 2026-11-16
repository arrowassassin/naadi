import {
  type ChapterDetail,
  type ChapterPositions,
  type CoursesResponse,
  getChapter,
  getCourses,
  getReadingPosition,
  putReadingPosition,
  type ReadingPositionIn,
  type ReadingPositionWrite,
  type ReportIssueIn,
  type ReportIssueOut,
  reportIssue,
} from "@naadi/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { installOfflineCacheReset, readOffline, writeOffline } from "./offlineCache";

installOfflineCacheReset();

export interface Offline<T> {
  data: T;
  /** true when the network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** A network failure (no HTTP response) rather than an answer from the server. */
function isUnreachable(e: unknown): boolean {
  return e instanceof ApiError && e.status === null && e.code === null;
}

async function withOfflineCopy<T>(
  userId: string,
  kind: string,
  id: string,
  load: () => Promise<T>,
): Promise<Offline<T>> {
  try {
    const data = await load();
    void writeOffline(userId, kind, id, data);
    return { data, fromCache: false };
  } catch (err) {
    if (isUnreachable(err)) {
      const saved = await readOffline<T>(userId, kind, id);
      if (saved !== undefined) return { data: saved, fromCache: true };
    }
    throw err;
  }
}

export interface CourseFilter {
  exam?: string;
  system?: string;
}

export const coursesKey = (userId: string | null, f: CourseFilter) =>
  ["courses", userId, f.exam ?? null, f.system ?? null] as const;
export const chapterKey = (userId: string | null, id: string) => ["chapter", userId, id] as const;
export const positionsKey = (userId: string | null, id: string) => ["reading-positions", userId, id] as const;

export function useCourses(filter: CourseFilter) {
  const userId = useUserId();
  return useQuery({
    queryKey: coursesKey(userId, filter),
    enabled: userId !== null,
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      withOfflineCopy(userId as string, "courses", `${filter.exam ?? ""}|${filter.system ?? ""}`, () =>
        authed<CoursesResponse>((headers) =>
          getCourses({ signal, headers, query: { exam: filter.exam, system: filter.system } }),
        ),
      ),
  });
}

export function useChapter(chapterId: string) {
  const userId = useUserId();
  return useQuery({
    queryKey: chapterKey(userId, chapterId),
    enabled: userId !== null,
    staleTime: 5 * 60_000,
    queryFn: ({ signal }) =>
      withOfflineCopy(userId as string, "chapter", chapterId, () =>
        authed<ChapterDetail>((headers) => getChapter({ signal, headers, path: { chapter_id: chapterId } })),
      ),
  });
}

export function usePositions(chapterId: string) {
  const userId = useUserId();
  return useQuery({
    queryKey: positionsKey(userId, chapterId),
    enabled: userId !== null,
    staleTime: 0,
    queryFn: ({ signal }) =>
      withOfflineCopy(userId as string, "positions", chapterId, () =>
        authed<ChapterPositions>((headers) =>
          getReadingPosition({ signal, headers, query: { chapter_id: chapterId } }),
        ),
      ),
  });
}

/** Save a position; the server keeps the newest by device time, and the cache follows what it stored. */
export function useSavePosition(chapterId: string) {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    // keepalive: the last save of a page that is being closed still reaches the server.
    mutationFn: ({ body, keepalive = false }: { body: ReadingPositionIn; keepalive?: boolean }) =>
      authed<ReadingPositionWrite>((headers) => putReadingPosition({ headers, body, keepalive })),
    onSuccess: (res) => {
      qc.setQueryData<Offline<ChapterPositions>>(positionsKey(userId, chapterId), (old) => {
        const prev = old?.data.sections ?? [];
        const sections = [res.position, ...prev.filter((p) => p.section_id !== res.position.section_id)].sort(
          (a, b) => Date.parse(b.client_updated_at) - Date.parse(a.client_updated_at),
        );
        const data = { chapter_id: chapterId, last: sections[0] ?? null, sections };
        if (userId) void writeOffline(userId, "positions", chapterId, data);
        return { data, fromCache: false };
      });
      void qc.invalidateQueries({ queryKey: ["courses", userId] });
    },
  });
}

export function useReportIssue() {
  return useMutation({
    mutationFn: (body: ReportIssueIn) => authed<ReportIssueOut>((headers) => reportIssue({ headers, body })),
  });
}
