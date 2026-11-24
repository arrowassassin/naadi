import type { SubjectCounts, VideoOut, VideosOverview, VideoTopicOut } from "@naadi/shared";
import { Button, cx, ErrorState, Skeleton } from "@naadi/ui";
import { useNavigate } from "@tanstack/react-router";
import { ChevronDown, CircleCheck, CircleDashed, CircleDot, Search as SearchIcon, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useRegisterPageContext } from "../assistant/pageContext";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import {
  type Step,
  useSubjectVideos,
  useToggleWatched,
  useVideoList,
  useVideosOverview,
  useVideoTopics,
  type VideoFilters,
} from "./api";
import { videoPageContext } from "./ask";
import { STYLES, type VideosSearch } from "./search";
import { VideoCard, videoTitle } from "./VideoCard";
import { relatedVideos, VideoPlayerPane } from "./VideoPlayerPane";

type T = ReturnType<typeof useUiText>;

const LAPTOP = "(min-width: 68.75rem)"; // the `laptop` breakpoint of tokens.css
const DEBOUNCE_MS = 300;

function useIsLaptop(): boolean {
  return useSyncExternalStore(
    (l) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const m = window.matchMedia(LAPTOP);
      m.addEventListener("change", l);
      return () => m.removeEventListener("change", l);
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(LAPTOP).matches,
    () => false,
  );
}

function errorMessage(t: T, err: unknown): { message: string; requestId: string | null } {
  if (err instanceof ApiError) {
    return { message: t(err.status === 429 ? "videos.error_busy" : "videos.error_body"), requestId: err.requestId };
  }
  return { message: t("videos.error_body"), requestId: null };
}

const CHIP = "inline-flex min-h-11 shrink-0 items-center rounded-pill px-4 text-[0.875rem]";
const chipClass = (on: boolean) =>
  cx(
    CHIP,
    on
      ? "border-0 bg-paper font-semibold text-ground"
      : "border border-line-control bg-transparent text-text hover:bg-surface-2",
  );

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={chipClass(on)}>
      {children}
    </button>
  );
}

const STEP_TABS: readonly (Step | "all")[] = ["step1", "step2ck", "all"];

/** Subjects in syllabus order for the chosen step (both steps: each name once). */
function subjectsFor(o: VideosOverview | undefined, step: Step | undefined): string[] {
  const out: string[] = [];
  for (const s of o?.subjects ?? []) if ((!step || s.step === step) && !out.includes(s.subject)) out.push(s.subject);
  return out;
}

function counts(o: VideosOverview | undefined, step: Step | undefined, subject: string | undefined) {
  const rows = (o?.subjects ?? []).filter(
    (s: SubjectCounts) => (!step || s.step === step) && (!subject || s.subject === subject),
  );
  return { watched: rows.reduce((n, s) => n + s.watched, 0), videos: rows.reduce((n, s) => n + s.videos, 0) };
}

/** Consecutive runs of one step + subject (the server orders by step, then subject in syllabus order). */
interface Group {
  key: string;
  /** The first video's media id: unique, and safe in an id attribute (subject names have spaces). */
  id: string;
  subject: string;
  step: Step;
  items: VideoOut[];
}

function groups(items: VideoOut[]): Group[] {
  const out: Group[] = [];
  for (const v of items) {
    const key = `${v.step}|${v.subject}`;
    const last = out.at(-1);
    if (last?.key === key) last.items.push(v);
    else out.push({ key, id: v.media_id, subject: v.subject, step: v.step, items: [v] });
  }
  return out;
}

function GroupHeading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="m-0 font-mono font-medium text-label text-text-3 uppercase tracking-[0.14em]">
      {children}
    </h2>
  );
}

function GridSkeleton() {
  return (
    <div aria-hidden="true" className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <Skeleton key={i} className="h-64 rounded-tile-sm" />
      ))}
    </div>
  );
}

const COVERAGE = {
  topic: { icon: CircleCheck, key: "videos.coverage_topic" },
  chapter: { icon: CircleDot, key: "videos.coverage_chapter" },
  gap: { icon: CircleDashed, key: "videos.coverage_gap" },
} as const;

function TopicsView({
  step,
  subject,
  onShow,
}: {
  step: Step | undefined;
  subject: string | undefined;
  onShow: (topic: VideoTopicOut) => void;
}) {
  const t = useUiText();
  const q = useVideoTopics(step, subject, true);
  const bySubject = useMemo(() => {
    const out = new Map<string, VideoTopicOut[]>();
    for (const x of q.data?.items ?? []) {
      const key = `${x.step}|${x.subject}`;
      out.set(key, [...(out.get(key) ?? []), x]);
    }
    return [...out.entries()];
  }, [q.data]);

  if (q.isPending) return <GridSkeleton />;
  if (q.isError && !q.data) {
    return (
      <ErrorState
        title={t("videos.error_title")}
        {...errorMessage(t, q.error)}
        onRetry={() => void q.refetch()}
        retryLabel={t("videos.retry")}
        retrying={q.isFetching}
      />
    );
  }
  if (!bySubject.length) return <p className="m-0 text-text-2">{t("videos.topics_empty")}</p>;
  return (
    <div className="flex flex-col gap-8" aria-busy={q.isFetching || undefined}>
      {bySubject.map(([key, topics]) => {
        const first = topics[0] as VideoTopicOut;
        const covered = topics.filter((x) => x.coverage === "topic").length;
        return (
          <section
            key={key}
            aria-labelledby={`tp-${first.id}`}
            className="flex flex-col gap-2 [content-visibility:auto]"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <GroupHeading id={`tp-${first.id}`}>
                {t("videos.group", { subject: first.subject, step: t(`videos.step.${first.step}`) })}
              </GroupHeading>
              <span className="text-text-3 text-ui-sm">
                {t("videos.topics_covered", { n: String(covered), total: String(topics.length) })}
              </span>
            </div>
            <ul className="m-0 flex list-none flex-col p-0">
              {topics.map((x) => {
                const c = COVERAGE[x.coverage];
                const Icon = c.icon;
                return (
                  <li
                    key={x.id}
                    data-coverage={x.coverage}
                    className="grid min-h-14 grid-cols-1 items-center gap-x-4 gap-y-1 border-line-soft border-t py-2 tablet:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="font-semibold [overflow-wrap:anywhere]">{x.title}</span>
                      <span className="text-text-3 text-ui-sm">
                        {[x.chapter, x.core ? t("videos.core") : null].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span className="flex flex-wrap items-center justify-between gap-3 tablet:justify-end">
                      <span
                        className={cx(
                          "inline-flex items-center gap-1.5 text-ui-sm",
                          x.coverage === "gap" ? "text-text-3" : "text-text-2",
                        )}
                      >
                        <Icon aria-hidden="true" size={16} />
                        {t(c.key)}
                      </span>
                      {x.coverage !== "gap" ? (
                        <button
                          type="button"
                          onClick={() => onShow(x)}
                          aria-label={t("videos.topic_show_label", { title: x.title })}
                          className="inline-flex min-h-11 items-center rounded-pill border border-line-control px-4 text-ui-sm text-text hover:bg-surface-2"
                        >
                          {t("videos.topic_show")}
                        </button>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function VideosPage({ search }: { search: VideosSearch }) {
  const t = useUiText();
  const navigate = useNavigate();
  const laptop = useIsLaptop();
  const overview = useVideosOverview();
  const toggle = useToggleWatched();
  const [failed, setFailed] = useState(false);
  const [text, setText] = useState(search.q ?? "");
  const opener = useRef<HTMLElement | null>(null);

  const set = (next: Partial<VideosSearch>, replace = true) => {
    const merged = { ...search, ...next };
    void navigate({ to: "/videos", search: merged.v ? merged : { ...merged, vs: undefined }, replace });
  };

  // Typing commits to the URL after a short pause; a URL change (back button) puts its query in the field.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only a change of the typed text schedules a commit
  useEffect(() => {
    if (text.trim() === (search.q ?? "")) return;
    const id = window.setTimeout(() => set({ q: text.trim() || undefined }), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: follow the URL only when it changes
  useEffect(() => {
    if ((search.q ?? "") !== text.trim()) setText(search.q ?? "");
  }, [search.q]);

  const title = t("videos.title");
  useEffect(() => {
    document.title = `${title} · Naadi`;
  }, [title]);

  const filters: VideoFilters = {
    step: search.step,
    subject: search.subject,
    q: search.q,
    channel: search.channel,
    style: search.style,
    core: search.core === 1,
    unwatched: search.unwatched === 1,
  };
  const topicsView = search.view === "topics";
  const list = useVideoList(filters);
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? 0;

  // The open video: from the list, else (opened from "More on this topic", outside the filters) from its subject's
  // catalogue, else the copy the student tapped while that catalogue loads.
  const [picked, setPicked] = useState<VideoOut | null>(null);
  const inList = search.v ? (items.find((v) => v.media_id === search.v) ?? null) : null;
  const pickedNow = picked && picked.media_id === search.v ? picked : null;
  // Where the open video lives (its subject's catalogue): the list, the tapped copy, or the URL (a shared link).
  const home = inList ?? pickedNow ?? (search.v ? search.vs : undefined) ?? null;
  const subjectAll = useSubjectVideos(home?.step ?? null, home?.subject ?? null);
  const shown = search.v
    ? (inList ?? subjectAll.data?.items.find((v) => v.media_id === search.v) ?? pickedNow ?? null)
    : null;
  const related = useMemo(
    () => (shown && subjectAll.data ? relatedVideos(shown, subjectAll.data.items) : null),
    [shown, subjectAll.data],
  );
  const missing =
    Boolean(search.v) &&
    !shown &&
    list.isSuccess &&
    !list.isFetching &&
    (!home || subjectAll.isSuccess || subjectAll.isError);

  useRegisterPageContext(
    videoPageContext(shown ? { media_id: shown.media_id, title: videoTitle(t, shown) } : null, title),
  );

  const open = (v: VideoOut) => {
    if (document.activeElement instanceof HTMLElement && !search.v) opener.current = document.activeElement;
    setPicked(v);
    set({ v: v.media_id, vs: { step: v.step, subject: v.subject } }, false);
  };
  const close = () => {
    set({ v: undefined });
    window.requestAnimationFrame(() => opener.current?.focus({ preventScroll: true }));
  };
  const onToggle = (v: VideoOut) => {
    setFailed(false);
    toggle.mutate(
      { video: { media_id: v.media_id, step: v.step, subject: v.subject }, watched: !v.watched },
      { onError: () => setFailed(true) },
    );
  };

  const c = counts(overview.data, search.step, search.subject);
  const subjects = subjectsFor(overview.data, search.step);
  const channels = overview.data?.channels ?? [];
  const filtered = Boolean(search.q || search.channel || search.style || search.core || search.unwatched);

  return (
    <section aria-labelledby="videos-h" className="flex flex-col gap-6 pt-6 pb-10 tablet:pt-10">
      <header className="flex flex-col gap-5">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <h1
            id="videos-h"
            className="m-0 font-display font-extrabold text-[clamp(3rem,1.5rem+6vw,7rem)] leading-[0.9] tracking-[-0.05em]"
          >
            {t("videos.headline_first")}
            <br />
            <span className="text-text-muted">{t("videos.headline_second")}</span>
          </h1>
          <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
            <legend className="sr-only">{t("videos.view_label")}</legend>
            <Toggle on={!topicsView} onClick={() => set({ view: undefined })}>
              {t("videos.view_browse")}
            </Toggle>
            <Toggle on={topicsView} onClick={() => set({ view: "topics", v: undefined })}>
              {t("videos.view_topics")}
            </Toggle>
          </fieldset>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
            <legend className="sr-only">{t("videos.step_label")}</legend>
            {STEP_TABS.map((s) => (
              <Toggle
                key={s}
                on={(search.step ?? "all") === s}
                onClick={() => set({ step: s === "all" ? undefined : s, subject: undefined, v: undefined })}
              >
                {t(`videos.step.${s}`)}
              </Toggle>
            ))}
          </fieldset>
          {overview.data ? (
            <p className="m-0 text-text-2 text-ui-sm" data-watched-count="">
              {t("videos.watched_count", { n: String(c.watched), total: String(c.videos) })}
            </p>
          ) : null}
        </div>
      </header>

      {subjects.length ? (
        <nav aria-label={t("videos.subjects_label")} className="-mx-(--gutter) overflow-x-auto px-(--gutter)">
          <ul className="m-0 flex w-max list-none gap-2 p-0 pb-1">
            <li>
              <Toggle on={!search.subject} onClick={() => set({ subject: undefined, v: undefined })}>
                {t("videos.subjects_all")}
              </Toggle>
            </li>
            {subjects.map((s) => (
              <li key={s}>
                <Toggle on={search.subject === s} onClick={() => set({ subject: s, v: undefined })}>
                  {s}
                </Toggle>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}

      {topicsView ? (
        <TopicsView
          step={search.step}
          subject={search.subject}
          onShow={(x) =>
            set(
              {
                view: undefined,
                step: x.step,
                subject: x.subject,
                q: (x.coverage === "topic" ? x.title : (x.chapter ?? x.title)).slice(0, 100),
                v: undefined,
              },
              false,
            )
          }
        />
      ) : (
        <>
          <search>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                set({ q: text.trim() || undefined });
              }}
              className="flex items-center gap-3 rounded-pill border border-line-control bg-surface-1 px-4"
            >
              <SearchIcon aria-hidden="true" size={20} className="shrink-0 text-text-2" />
              <input
                type="search"
                value={text}
                onChange={(e) => setText(e.target.value)}
                aria-label={t("videos.search_label")}
                placeholder={t("videos.search_placeholder")}
                maxLength={100}
                autoComplete="off"
                enterKeyHint="search"
                className="min-h-12 min-w-0 flex-1 appearance-none border-0 bg-transparent text-text outline-none placeholder:text-text-3 [&::-webkit-search-cancel-button]:hidden"
              />
              {text ? (
                <button
                  type="button"
                  aria-label={t("videos.search_clear")}
                  onClick={() => {
                    setText("");
                    set({ q: undefined });
                  }}
                  className="-mr-2 flex size-11 shrink-0 items-center justify-center rounded-full text-text hover:bg-surface-2"
                >
                  <X aria-hidden="true" size={18} />
                </button>
              ) : null}
            </form>
          </search>

          <fieldset className="m-0 flex flex-wrap items-center gap-2 border-0 p-0">
            <legend className="sr-only">{t("videos.filters_label")}</legend>
            {STYLES.map((s) => (
              <Toggle
                key={s}
                on={search.style === s}
                onClick={() => set({ style: search.style === s ? undefined : s, v: undefined })}
              >
                {t(`videos.style.${s}`)}
              </Toggle>
            ))}
            <Toggle on={search.core === 1} onClick={() => set({ core: search.core ? undefined : 1, v: undefined })}>
              {t("videos.filter_core")}
            </Toggle>
            <Toggle
              on={search.unwatched === 1}
              onClick={() => set({ unwatched: search.unwatched ? undefined : 1, v: undefined })}
            >
              {t("videos.filter_unwatched")}
            </Toggle>
            {channels.length ? (
              <span className={cx("relative inline-flex max-w-full", search.channel ? "text-ground" : "text-text")}>
                <select
                  aria-label={t("videos.channel_label")}
                  value={search.channel ?? ""}
                  onChange={(e) => set({ channel: e.target.value || undefined, v: undefined })}
                  className={cx(chipClass(Boolean(search.channel)), "max-w-full appearance-none truncate pr-10")}
                >
                  <option value="">{t("videos.channel_all")}</option>
                  {channels.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {t("videos.channel_option", { name: ch.name, n: String(ch.videos) })}
                    </option>
                  ))}
                </select>
                <ChevronDown
                  aria-hidden="true"
                  size={16}
                  className="pointer-events-none absolute top-1/2 right-4 -translate-y-1/2"
                />
              </span>
            ) : null}
          </fieldset>

          {failed ? (
            <p role="alert" className="m-0 text-amber text-ui">
              {t("videos.toggle_failed")}
            </p>
          ) : null}

          <div
            className={cx(
              "grid gap-6",
              (shown || missing) && laptop && "grid-cols-[minmax(0,1fr)_minmax(24rem,30rem)]",
            )}
          >
            <div className="flex min-w-0 flex-col gap-8" aria-busy={list.isFetching || undefined}>
              <p role="status" aria-live="polite" className="sr-only">
                {list.isFetching ? t("videos.loading") : list.data ? t("videos.count", { n: String(total) }) : ""}
              </p>
              {list.isPending ? (
                <GridSkeleton />
              ) : list.isError && !list.data ? (
                <ErrorState
                  title={t("videos.error_title")}
                  {...errorMessage(t, list.error)}
                  onRetry={() => void list.refetch()}
                  retryLabel={t("videos.retry")}
                  retrying={list.isFetching}
                />
              ) : items.length === 0 ? (
                <section aria-label={t("videos.empty_title")} className="flex max-w-prose flex-col gap-2">
                  <p className="m-0 font-bold font-display text-headline">{t("videos.empty_title")}</p>
                  <p className="m-0 text-text-2">{t(filtered ? "videos.empty_filtered" : "videos.empty_body")}</p>
                </section>
              ) : (
                <>
                  {groups(items).map((g) => (
                    <section key={g.id} aria-labelledby={`vg-${g.id}`} className="flex flex-col gap-3">
                      <GroupHeading id={`vg-${g.id}`}>
                        {t("videos.group", { subject: g.subject, step: t(`videos.step.${g.step}`) })}
                      </GroupHeading>
                      <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4 p-0">
                        {g.items.map((v) => (
                          <VideoCard
                            key={v.media_id}
                            v={v}
                            selected={v.media_id === search.v}
                            onOpen={open}
                            onToggle={onToggle}
                          />
                        ))}
                      </ul>
                    </section>
                  ))}
                  <div className="flex flex-wrap items-center gap-4">
                    <span className="text-text-3 text-ui-sm">
                      {t("videos.showing", { n: String(items.length), total: String(total) })}
                    </span>
                    {list.hasNextPage ? (
                      <Button
                        variant="secondary"
                        disabled={list.isFetchingNextPage}
                        onClick={() => void list.fetchNextPage()}
                      >
                        {t(list.isFetchingNextPage ? "videos.loading" : "videos.load_more")}
                      </Button>
                    ) : null}
                  </div>
                  {list.isFetchNextPageError ? (
                    <p role="alert" className="m-0 text-amber text-ui">
                      {t("videos.more_failed")}
                    </p>
                  ) : null}
                </>
              )}
            </div>
            {laptop ? (
              <VideoPlayerPane
                video={shown}
                missing={missing}
                layout="side"
                related={related}
                onClose={close}
                onToggle={onToggle}
                onOpen={open}
              />
            ) : null}
          </div>
          {laptop ? null : (
            <VideoPlayerPane
              video={shown}
              missing={missing}
              layout="sheet"
              related={related}
              onClose={close}
              onToggle={onToggle}
              onOpen={open}
            />
          )}
        </>
      )}
    </section>
  );
}
