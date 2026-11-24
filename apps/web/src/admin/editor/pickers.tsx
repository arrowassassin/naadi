import { cx } from "@naadi/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Check, FileText, Image, ListChecks, Play, Search } from "lucide-react";
import { useState } from "react";
import { type AdminMedia, type AdminQuestionSummary, api, type MediaKind, qk } from "../api";
import {
  Empty,
  Failure,
  FilterPills,
  fmtSeconds,
  INPUT,
  Loading,
  MoreButton,
  StatusChip,
  useT,
} from "../components/ui";

/** Every kind a media row can have (the filter list, MEDIA_KINDS, leaves out the catalogue's video files). */
export const MEDIA_KIND_KEYS: Record<AdminMedia["kind"], string> = {
  svg: "admin.media.kind.svg",
  image: "admin.media.kind.image",
  video_own: "admin.media.kind.video_own",
  video_youtube: "admin.media.kind.video_youtube",
  video_file: "admin.media.kind.video_file",
  audio: "admin.media.kind.audio",
  pdf: "admin.media.kind.pdf",
  link: "admin.media.kind.link",
};

export function MediaThumb({ m, className }: { m: AdminMedia; className?: string }) {
  const t = useT();
  if ((m.kind === "svg" || m.kind === "image") && m.preview_url) {
    return (
      <span className={cx("flex items-center justify-center overflow-hidden rounded-inner bg-white", className)}>
        <img
          src={m.preview_url}
          alt={m.caption ?? m.title ?? t("admin.media.no_title")}
          width={m.width ?? 4}
          height={m.height ?? 3}
          loading="lazy"
          className="block h-full w-full object-contain"
        />
      </span>
    );
  }
  const Icon = m.kind === "pdf" ? FileText : m.kind.startsWith("video") ? Play : Image;
  return (
    <span className={cx("flex items-center justify-center rounded-inner bg-surface-2 text-text-2", className)}>
      <Icon aria-hidden="true" size={28} />
    </span>
  );
}

/** Pick one media asset of the given kinds (the figure, video and PDF dialogs). */
export function MediaPicker({
  kinds,
  value,
  onPick,
}: {
  kinds: readonly MediaKind[];
  value: string | null;
  onPick: (m: AdminMedia) => void;
}) {
  const t = useT();
  const [kind, setKind] = useState<MediaKind>(kinds[0] ?? "svg");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const params = { kind, q: q || undefined, limit: 24 };
  const list = useInfiniteQuery({
    queryKey: [...qk.media(params), "picker"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.media({ ...params, cursor: pageParam }, signal),
    getNextPageParam: (p) => p.next_cursor,
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-4">
      {kinds.length > 1 ? (
        <FilterPills
          label={t("admin.media.kind_label")}
          value={kind}
          options={kinds.map((k) => ({ value: k, label: t(MEDIA_KIND_KEYS[k]) }))}
          onChange={setKind}
        />
      ) : null}
      <search className="relative flex">
        <label className="flex w-full">
          <span className="sr-only">{t("admin.media.search")}</span>
          <Search
            aria-hidden="true"
            size={16}
            className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
          />
          <input
            type="search"
            value={draft}
            placeholder={t("admin.media.search")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // This picker sits inside the dialog's own form: Enter searches, it never submits that form.
              if (e.key === "Enter") {
                e.preventDefault();
                setQ(draft.trim());
              }
            }}
            className={cx(INPUT, "pl-10")}
          />
        </label>
      </search>
      {list.isPending ? (
        <Loading rows={2} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <Empty title={t("admin.media.empty")} body={t("admin.media.picker_empty_body")} />
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 tablet:grid-cols-2">
          {items.map((m) => {
            const on = value === m.id;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => onPick(m)}
                  className={cx(
                    "flex w-full items-center gap-3 rounded-block border p-3 text-left text-text",
                    on ? "border-paper bg-surface-2" : "border-line-soft bg-surface-1 hover:border-line-control",
                  )}
                >
                  <MediaThumb m={m} className="size-16 shrink-0" />
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="break-words font-semibold">
                      {m.title ?? m.caption ?? t("admin.media.no_title")}
                    </span>
                    <span className="break-all font-mono text-label-sm text-text-3">{m.id}</span>
                    <span className="flex flex-wrap items-center gap-2">
                      <StatusChip status={m.review_status} prefix={t("admin.media.review_label")} />
                      {m.duration_s ? <span className="text-ui-sm text-text-2">{fmtSeconds(m.duration_s)}</span> : null}
                    </span>
                  </span>
                  {on ? (
                    <span className="flex items-center gap-1 text-ui-sm font-semibold">
                      <Check aria-hidden="true" size={16} />
                      {t("admin.action.selected")}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <MoreButton
        show={Boolean(list.hasNextPage)}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
    </div>
  );
}

/** Pick one question to embed (stem preview only; never the answer). */
export function QuestionPicker({
  exam,
  value,
  onPick,
}: {
  exam: string | undefined;
  value: string | null;
  onPick: (q: AdminQuestionSummary) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const params = { exam, q: q || undefined, limit: 20 };
  const list = useInfiniteQuery({
    queryKey: [...qk.questions(params), "picker"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.questions({ ...params, cursor: pageParam }, signal),
    getNextPageParam: (p) => p.next_cursor,
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-4">
      <search className="relative flex">
        <label className="flex w-full">
          <span className="sr-only">{t("admin.questions.search")}</span>
          <Search
            aria-hidden="true"
            size={16}
            className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
          />
          <input
            type="search"
            value={draft}
            placeholder={t("admin.questions.search")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // This picker sits inside the dialog's own form: Enter searches, it never submits that form.
              if (e.key === "Enter") {
                e.preventDefault();
                setQ(draft.trim());
              }
            }}
            className={cx(INPUT, "pl-10")}
          />
        </label>
      </search>
      {list.isPending ? (
        <Loading rows={2} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <Empty title={t("admin.questions.empty")} />
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((it) => {
            const on = value === it.id;
            return (
              <li key={it.id}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => onPick(it)}
                  className={cx(
                    "flex w-full items-start gap-3 rounded-block border p-3 text-left text-text",
                    on ? "border-paper bg-surface-2" : "border-line-soft bg-surface-1 hover:border-line-control",
                  )}
                >
                  <ListChecks aria-hidden="true" size={18} className="mt-1 shrink-0 text-text-2" />
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="break-words">{it.stem_text}</span>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-label-sm text-text-3">{it.id}</span>
                      <StatusChip status={it.status} prefix={t("admin.field.status")} />
                    </span>
                  </span>
                  {on ? <Check aria-hidden="true" size={16} className="mt-1 shrink-0" /> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <MoreButton
        show={Boolean(list.hasNextPage)}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
    </div>
  );
}
