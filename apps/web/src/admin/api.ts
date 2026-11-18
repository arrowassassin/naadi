/**
 * Admin console API client (segment 19). Every call goes to /api/admin/* with the signed-in admin's access token;
 * the server checks `require_admin` on every route (a non-admin gets 403, never data).
 *
 * Config endpoints already have generated contracts (@naadi/shared zod), so their responses are parsed with them.
 * The other admin endpoints are typed here against the S19 API shapes until `pnpm gen:contracts` publishes their
 * generated schemas; switching is a type-import change, the call sites stay the same.
 */
import type {
  AdminChapter,
  AdminChapterDetail,
  AdminChapterList,
  AdminMedia,
  AdminMediaList,
  AdminOverview,
  AdminQuestion,
  AdminQuestionList,
  AdminQuestionSummary,
  AdminReport,
  AdminReportList,
  AdminSection,
  AdminSource,
  AdminSourceList,
  AgentCron,
  AgentCronList,
  AgentRun,
  AgentRunList,
  AgentUsage,
  BlueprintArea,
  BlueprintCoverage,
  CancelOut,
  ChapterCreate,
  ChapterPatch,
  ConfigEntry,
  ConfigHistory,
  ConfigKeyList,
  CoverageReport,
  DiffBlock,
  DraftSave,
  EditorQuestion,
  FieldError,
  LintFinding,
  LintResult,
  MediaPatch,
  QuestionCreate,
  QuestionRevisionItem,
  QuestionRevisionList,
  QueueCount,
  QueueCounts,
  QueueDecision,
  QueueItem,
  QueuePage,
  QueueRef,
  ReportLink,
  Revision,
  RevisionDiff,
  RevisionList,
  RevisionSummary,
  SectionEditor,
  SectionMediaIn,
  SectionMediaOut,
  TaxonomyList,
  TaxonomyNode,
  VideoAdd,
  VideoLookupOut,
} from "@naadi/shared";
import * as Z from "@naadi/shared";
import { ApiError } from "../auth/apiError";
import { accessToken, refreshAccessToken, signOut } from "../auth/session";

export type { ConfigEntry, ConfigHistory, ConfigKeyList };

// ---------------------------------------------------------------------------------------------------- errors

/** A failed admin call: the server's envelope including `details` (validation paths, gate refusals). */
export class AdminApiError extends ApiError {
  readonly details: FieldError[];
  constructor(
    message: string,
    status: number | null,
    code: string | null,
    requestId: string | null,
    details: FieldError[] = [],
  ) {
    super(message, status, code, requestId);
    this.name = "AdminApiError";
    this.details = details;
  }

  /** `gate_refused` 409s: the licence/publish gate reason and the offending media or ref id. */
  gate(): { reason: string; refId: string } | null {
    if (this.code !== "gate_refused") return null;
    const d = this.details.find((x) => x.type === "gate" || x.loc[0] === "gate");
    if (!d) return { reason: "unknown", refId: "" };
    return { reason: String(d.loc[1] ?? "unknown"), refId: d.msg };
  }

  /** 422 validation: each failing field as a dotted path (body.content.3.attrs.mediaId) and its message. */
  fields(): { path: string; msg: string }[] {
    return this.details.filter((d) => d.type !== "gate").map((d) => ({ path: d.loc.join("."), msg: d.msg }));
  }
}

/** Error codes the client words itself (the server message is shown only as detail). */
export const UNREACHABLE_CODE = "unreachable";
export const CONTRACT_CODE = "contract";

export function isAdminError(e: unknown): e is AdminApiError {
  return e instanceof AdminApiError;
}

export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}

export function isConflict(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && e.code !== "gate_refused";
}

// ---------------------------------------------------------------------------------------------------- transport

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOpts {
  query?: Query;
  body?: unknown;
  /** A raw body (file upload) sent with its own content type. */
  raw?: Blob;
  signal?: AbortSignal;
  /** zod-like parser for the response body (generated contracts). */
  parse?: (data: unknown) => unknown;
}

function url(path: string, query?: Query): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return `/api/admin${path}${s ? `?${s}` : ""}`;
}

/** Encodes one path segment (ids come from the server but are never trusted to be URL-safe). */
export const seg = (v: string | number) => encodeURIComponent(String(v));

async function send(method: string, path: string, opts: RequestOpts, token: string): Promise<Response> {
  const headers: Record<string, string> = { authorization: `Bearer ${token}`, accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.raw) {
    body = opts.raw;
    headers["content-type"] = opts.raw.type || "application/octet-stream";
  } else if (opts.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  return fetch(url(path, opts.query), { method, headers, body, signal: opts.signal, credentials: "same-origin" });
}

async function errorOf(res: Response): Promise<AdminApiError> {
  const rid = res.headers.get("x-request-id");
  let json: unknown = null;
  try {
    json = await res.json();
  } catch {
    json = null;
  }
  const env = Z.zErrorResponse.safeParse(json);
  if (env.success) {
    const e = env.data.error;
    return new AdminApiError(e.message, res.status, e.code, e.request_id ?? rid, e.details ?? []);
  }
  return new AdminApiError(`HTTP ${res.status}`, res.status, null, rid);
}

async function attempt(method: string, path: string, opts: RequestOpts, token: string): Promise<Response> {
  try {
    return await send(method, path, opts, token);
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
    throw new AdminApiError("", null, UNREACHABLE_CODE, null);
  }
}

/**
 * One admin request. An expired token is refreshed once and the call retried; any other 401 ends the local
 * session (the router then sends the user to sign in), like `authed` for the generated SDK.
 */
export async function adminRequest<T>(method: string, path: string, opts: RequestOpts = {}): Promise<T> {
  const token = await accessToken();
  if (!token) throw new AdminApiError("", 401, "unauthorized", null);
  let res = await attempt(method, path, opts, token);
  if (res.status === 401) {
    const err = await errorOf(res);
    const fresh = err.code === "token_expired" ? await refreshAccessToken() : null;
    if (!fresh) {
      await signOut("local");
      throw err;
    }
    res = await attempt(method, path, opts, fresh);
    if (res.status === 401) {
      await signOut("local");
      throw await errorOf(res);
    }
  }
  if (!res.ok) throw await errorOf(res);
  if (res.status === 204) return undefined as T;
  let data: unknown;
  try {
    data = await res.json();
  } catch {
    throw new AdminApiError("", res.status, CONTRACT_CODE, res.headers.get("x-request-id"));
  }
  if (opts.parse) {
    try {
      return opts.parse(data) as T;
    } catch {
      throw new AdminApiError("", res.status, CONTRACT_CODE, res.headers.get("x-request-id"));
    }
  }
  return data as T;
}

// ---------------------------------------------------------------------------------------------------- types

export type {
  AdminChapter,
  AdminMedia,
  AdminOverview,
  AdminQuestion,
  AdminQuestionSummary,
  AdminReport as ReportRow,
  AdminSection,
  AdminSource as SourceRow,
  AgentCron,
  AgentRun,
  AgentUsage,
  BlueprintArea,
  BlueprintCoverage as Blueprint,
  CoverageReport as Coverage,
  DiffBlock,
  EditorQuestion,
  LintFinding,
  QuestionRevisionItem,
  QueueCount,
  QueueDecision,
  QueueItem,
  QueuePage,
  QueueRef,
  ReportLink,
  Revision,
  RevisionDiff,
  RevisionSummary,
  SectionEditor,
  TaxonomyNode,
  VideoLookupOut as YoutubeLookup,
};

export const CHAPTER_STATUSES = ["draft", "in_review", "published", "retired"] as const;
export type ChapterStatus = (typeof CHAPTER_STATUSES)[number];
export const QUESTION_STATUSES = ["draft", "auto_checked", "in_review", "published", "retired"] as const;
export type QuestionStatus = (typeof QUESTION_STATUSES)[number];
export const MEDIA_KINDS = ["svg", "image", "video_own", "video_youtube", "audio", "pdf", "link"] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];
export const REVIEW_STATUSES = ["unreviewed", "approved", "rejected"] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];
export const REPORT_STATUSES = ["open", "triaged", "resolved", "dismissed"] as const;
export type ReportStatus = (typeof REPORT_STATUSES)[number];
export const EXAMS = ["step1", "step2ck"] as const;
export type Exam = "step1" | "step2ck" | "step3";
export const DIFFICULTIES = ["easy", "medium", "hard"] as const;
export type Difficulty = (typeof DIFFICULTIES)[number];
export const OPTION_LETTERS = ["A", "B", "C", "D", "E"] as const;
export const COVERAGE_COUNTS = ["sections", "videos", "questions", "cards"] as const;
export type CoverageCountKind = (typeof COVERAGE_COUNTS)[number];
export type CoverageStatus = "gap" | "thin" | "covered";

/** The editable fields of a question (create and update share them; update adds expected_version). */
export type QuestionFields = Omit<QuestionCreate, "id">;

// ---------------------------------------------------------------------------------------------------- calls

type Parser<T> = { parse: (d: unknown) => T };
const g = <T>(path: string, z: Parser<T>, query?: Query, signal?: AbortSignal) =>
  adminRequest<T>("GET", path, { query, signal, parse: (d) => z.parse(d) });
const w = <T>(method: string, path: string, z: Parser<T>, body: unknown = {}) =>
  adminRequest<T>(method, path, { body, parse: (d) => z.parse(d) });

export type ChapterQuery = { exam?: string; status?: string; q?: string };
export type MediaQuery = { kind?: string; review_status?: string; q?: string; limit?: number; cursor?: string | null };
export type QuestionQuery = {
  exam?: string;
  status?: string;
  q?: string;
  chapter_id?: string;
  taxonomy_id?: string;
  limit?: number;
  cursor?: string | null;
};
export type UploadMeta = {
  source_id: string;
  title: string;
  caption?: string | null;
  attribution?: string | null;
  taxonomy_ids?: string[];
};

export const api = {
  overview: (signal?: AbortSignal) => g<AdminOverview>("/overview", Z.zGetAdminOverviewResponse, undefined, signal),

  chapters: (q: ChapterQuery, signal?: AbortSignal) =>
    g<AdminChapterList>("/chapters", Z.zListAdminChaptersResponse, q, signal),
  createChapter: (body: ChapterCreate) => w<AdminChapter>("POST", "/chapters", Z.zCreateAdminChapterResponse, body),
  chapter: (id: string, signal?: AbortSignal) =>
    g<AdminChapterDetail>(`/chapters/${seg(id)}`, Z.zGetAdminChapterResponse, undefined, signal),
  patchChapter: (id: string, body: ChapterPatch) =>
    w<AdminChapter>("PATCH", `/chapters/${seg(id)}`, Z.zPatchAdminChapterResponse, body),
  chapterStatus: (id: string, body: { status: ChapterStatus; note?: string | null }) =>
    w<AdminChapter>("POST", `/chapters/${seg(id)}/status`, Z.zSetAdminChapterStatusResponse, body),
  reorderChapters: (body: { exam: Exam; ids: string[] }) =>
    w<AdminChapterList>("POST", "/chapters/reorder", Z.zReorderAdminChaptersResponse, body),
  createSection: (chapterId: string, body: { heading: string; taxonomy_ids?: string[] }) =>
    w<AdminSection>("POST", `/chapters/${seg(chapterId)}/sections`, Z.zCreateAdminSectionResponse, body),
  reorderSections: (chapterId: string, ids: string[]) =>
    w<AdminChapterDetail>("POST", `/chapters/${seg(chapterId)}/sections/reorder`, Z.zReorderAdminSectionsResponse, {
      ids,
    }),
  patchSection: (sid: string, body: { heading?: string; taxonomy_ids?: string[] }) =>
    w<AdminSection>("PATCH", `/sections/${seg(sid)}`, Z.zPatchAdminSectionResponse, body),

  sectionEditor: (sid: string, signal?: AbortSignal) =>
    g<SectionEditor>(`/sections/${seg(sid)}/editor`, Z.zGetAdminSectionEditorResponse, undefined, signal),
  putDraft: (sid: string, body: DraftSave) =>
    w<Revision>("PUT", `/sections/${seg(sid)}/draft`, Z.zSaveAdminSectionDraftResponse, body),
  discardDraft: (sid: string) => adminRequest<void>("DELETE", `/sections/${seg(sid)}/draft`),
  publish: (sid: string, body: { revision_id: number; expected_updated_at: string; note?: string | null }) =>
    w<Revision>("POST", `/sections/${seg(sid)}/publish`, Z.zPublishAdminSectionResponse, body),
  revisions: (sid: string, signal?: AbortSignal) =>
    g<RevisionList>(`/sections/${seg(sid)}/revisions`, Z.zListAdminSectionRevisionsResponse, { limit: 50 }, signal),
  revision: (rid: number, signal?: AbortSignal) =>
    g<Revision>(`/revisions/${seg(rid)}`, Z.zGetAdminRevisionResponse, undefined, signal),
  revisionDiff: (rid: number, against: string, signal?: AbortSignal) =>
    g<RevisionDiff>(`/revisions/${seg(rid)}/diff`, Z.zDiffAdminRevisionResponse, { against }, signal),
  rollback: (rid: number, note?: string | null) =>
    w<Revision>("POST", `/revisions/${seg(rid)}/rollback`, Z.zRollbackAdminRevisionResponse, { note: note ?? null }),

  media: (q: MediaQuery, signal?: AbortSignal) => g<AdminMediaList>("/media", Z.zListAdminMediaResponse, q, signal),
  /** One route for figures and PDFs: the raw file as the body, metadata in the query (no multipart). */
  uploadMedia: (file: Blob, meta: UploadMeta) =>
    adminRequest<AdminMedia>("POST", "/media/files", {
      raw: file,
      query: {
        source_id: meta.source_id,
        title: meta.title,
        caption: meta.caption ?? undefined,
        attribution: meta.attribution ?? undefined,
        taxonomy_ids: meta.taxonomy_ids?.length ? meta.taxonomy_ids.join(",") : undefined,
      },
      parse: (d) => Z.zUploadAdminMediaResponse.parse(d),
    }),
  youtubeLookup: (u: string) =>
    w<VideoLookupOut>("POST", "/media/youtube/lookup", Z.zLookupAdminYoutubeResponse, { url: u }),
  addYoutube: (body: VideoAdd) => w<AdminMedia>("POST", "/media/youtube", Z.zAddAdminYoutubeResponse, body),
  patchMedia: (id: string, body: MediaPatch) =>
    w<AdminMedia>("PATCH", `/media/${seg(id)}`, Z.zPatchAdminMediaResponse, body),
  reviewMedia: (id: string, body: { status: "approved" | "rejected"; note?: string | null }) =>
    w<AdminMedia>("POST", `/media/${seg(id)}/review`, Z.zReviewAdminMediaResponse, body),
  attachMedia: (sid: string, mid: string, body: SectionMediaIn) =>
    w<SectionMediaOut>("PUT", `/sections/${seg(sid)}/media/${seg(mid)}`, Z.zAttachAdminSectionMediaResponse, body),
  detachMedia: (sid: string, mid: string) => adminRequest<void>("DELETE", `/sections/${seg(sid)}/media/${seg(mid)}`),

  questions: (q: QuestionQuery, signal?: AbortSignal) =>
    g<AdminQuestionList>("/questions", Z.zListAdminQuestionsResponse, q, signal),
  question: (id: string, signal?: AbortSignal) =>
    g<AdminQuestion>(`/questions/${seg(id)}`, Z.zGetAdminQuestionResponse, undefined, signal),
  createQuestion: (body: QuestionFields & { id?: string | null }) =>
    w<AdminQuestion>("POST", "/questions", Z.zCreateAdminQuestionResponse, body),
  putQuestion: (id: string, body: QuestionFields & { expected_version: string }) =>
    w<AdminQuestion>("PUT", `/questions/${seg(id)}`, Z.zUpdateAdminQuestionResponse, body),
  questionStatus: (id: string, body: { status: QuestionStatus; note?: string | null; expected_version: string }) =>
    w<AdminQuestion>("POST", `/questions/${seg(id)}/status`, Z.zSetAdminQuestionStatusResponse, body),
  questionLint: (id: string, signal?: AbortSignal) =>
    g<LintResult>(`/questions/${seg(id)}/lint`, Z.zLintAdminQuestionResponse, undefined, signal),
  questionRevisions: (id: string, signal?: AbortSignal) =>
    g<QuestionRevisionList>(
      `/questions/${seg(id)}/revisions`,
      Z.zListAdminQuestionRevisionsResponse,
      undefined,
      signal,
    ),

  taxonomy: (exam: string | undefined, signal?: AbortSignal) =>
    g<TaxonomyList>("/taxonomy", Z.zListAdminTaxonomyResponse, { exam }, signal),
  sources: (signal?: AbortSignal) => g<AdminSourceList>("/sources", Z.zListAdminSourcesResponse, undefined, signal),

  configList: (signal?: AbortSignal) => g<ConfigKeyList>("/config", Z.zListAppConfigResponse, undefined, signal),
  config: (key: string, signal?: AbortSignal) =>
    g<ConfigEntry>(`/config/${seg(key)}`, Z.zGetAppConfigResponse, undefined, signal),
  putConfig: (key: string, body: { value: unknown; expected_updated_at: string | null; admin_edited?: boolean }) =>
    w<ConfigEntry>("PUT", `/config/${seg(key)}`, Z.zPutAppConfigResponse, body),
  configHistory: (key: string, signal?: AbortSignal) =>
    g<ConfigHistory>(`/config/${seg(key)}/history`, Z.zGetAppConfigHistoryResponse, { limit: 50 }, signal),
  revertConfig: (key: string, body: { history_id: number; expected_updated_at: string | null }) =>
    w<ConfigEntry>("POST", `/config/${seg(key)}/revert`, Z.zRevertAppConfigResponse, body),

  coverage: (exam: string, signal?: AbortSignal) =>
    g<CoverageReport>("/coverage", Z.zGetAdminCoverageResponse, { exam }, signal),
  blueprint: (exam: string, signal?: AbortSignal) =>
    g<BlueprintCoverage>("/coverage/blueprint", Z.zGetAdminBlueprintCoverageResponse, { exam }, signal),

  agentRuns: (q: { graph?: string; status?: string; limit?: number }, signal?: AbortSignal) =>
    g<AgentRunList>("/agent/runs", Z.zListAdminAgentRunsResponse, q, signal),
  cancelRun: (threadId: string, runId: string) =>
    w<CancelOut>("POST", `/agent/runs/${seg(threadId)}/${seg(runId)}/cancel`, Z.zCancelAdminAgentRunResponse),
  agentCrons: (signal?: AbortSignal) =>
    g<AgentCronList>("/agent/crons", Z.zListAdminAgentCronsResponse, undefined, signal),
  agentUsage: (days: number, signal?: AbortSignal) =>
    g<AgentUsage>("/agent/usage", Z.zGetAdminAgentUsageResponse, { days }, signal),

  queues: (signal?: AbortSignal) => g<QueueCounts>("/queues", Z.zListAdminQueuesResponse, undefined, signal),
  queue: (kind: string, q: { limit?: number; cursor?: string | null }, signal?: AbortSignal) =>
    g<QueuePage>(`/queues/${seg(kind)}`, Z.zGetAdminQueueResponse, q, signal),
  approve: (kind: string, id: string, body: { note?: string | null; params?: Record<string, unknown> }) =>
    w<QueueDecision>("POST", `/queues/${seg(kind)}/${seg(id)}/approve`, Z.zApproveAdminQueueItemResponse, body),
  reject: (kind: string, id: string, body: { note?: string | null }) =>
    w<QueueDecision>("POST", `/queues/${seg(kind)}/${seg(id)}/reject`, Z.zRejectAdminQueueItemResponse, body),

  reports: (status: string | undefined, signal?: AbortSignal) =>
    g<AdminReportList>("/reports", Z.zListAdminReportsResponse, { status, limit: 100 }, signal),
  reportStatus: (id: string, body: { status: ReportStatus; note?: string | null }) =>
    w<AdminReport>("POST", `/reports/${seg(id)}/status`, Z.zSetAdminReportStatusResponse, body),
};

/** Query keys, namespaced under "admin" so one invalidation refreshes the console after a write. */
export const qk = {
  all: ["admin"] as const,
  overview: ["admin", "overview"] as const,
  chapters: (q: object) => ["admin", "chapters", q] as const,
  chapter: (id: string) => ["admin", "chapter", id] as const,
  editor: (sid: string) => ["admin", "section-editor", sid] as const,
  revisions: (sid: string) => ["admin", "revisions", sid] as const,
  revision: (rid: number) => ["admin", "revision", rid] as const,
  diff: (rid: number, against: string) => ["admin", "diff", rid, against] as const,
  media: (q: object) => ["admin", "media", q] as const,
  questions: (q: object) => ["admin", "questions", q] as const,
  question: (id: string) => ["admin", "question", id] as const,
  lint: (id: string) => ["admin", "lint", id] as const,
  questionRevisions: (id: string) => ["admin", "question-revisions", id] as const,
  taxonomy: (exam: string | undefined) => ["admin", "taxonomy", exam ?? "all"] as const,
  sources: ["admin", "sources"] as const,
  configList: ["admin", "config"] as const,
  config: (key: string) => ["admin", "config", key] as const,
  configHistory: (key: string) => ["admin", "config-history", key] as const,
  coverage: (exam: string) => ["admin", "coverage", exam] as const,
  blueprint: (exam: string) => ["admin", "blueprint", exam] as const,
  runs: (q: object) => ["admin", "agent-runs", q] as const,
  crons: ["admin", "agent-crons"] as const,
  usage: (days: number) => ["admin", "agent-usage", days] as const,
  queues: ["admin", "queues"] as const,
  queue: (kind: string) => ["admin", "queue", kind] as const,
  reports: (status: string | undefined) => ["admin", "reports", status ?? "all"] as const,
};
