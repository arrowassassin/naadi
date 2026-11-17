import type { LibraryChapter } from "@naadi/shared";
import { cx, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { FileText, Inbox } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";
import { AnkiTile } from "../anki/AnkiTile";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { requestErrorKey, uploads, useLibrary, useUploadsVersion } from "./api";
import { ConfirmCard, VaultCard } from "./ConfirmCard";
import { cardGridClass, FileCard } from "./FileCard";
import { Intake } from "./Intake";
import { confirmGroups, FILTERS, type Filter, matchesFilter, statusOf, vaultPrompts } from "./model";
import { StorageMeter } from "./StorageMeter";

const GeneratedLibrary = lazy(() =>
  import("../generation/GeneratedLibrary").then((m) => ({ default: m.GeneratedLibrary })),
);

function Headline() {
  const t = useUiText();
  return (
    <h1
      id="library-h"
      className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,8.5rem)] leading-[0.88] tracking-[-0.05em]"
    >
      {t("library.headline_first")}
      <br />
      <span className="text-text-muted">{t("library.headline_second")}</span>
    </h1>
  );
}

function Loading() {
  const t = useUiText();
  return (
    <section aria-labelledby="library-h" aria-busy="true" className="flex flex-col gap-8 pt-6 tablet:pt-10">
      <Headline />
      <p role="status" className="sr-only">
        {t("library.loading")}
      </p>
      <div className="flex flex-wrap gap-5">
        <Skeleton className="h-[260px] min-w-0 flex-[2_1_480px] rounded-tile" />
        <Skeleton className="h-[260px] min-w-0 flex-[1_1_320px] rounded-tile" />
      </div>
      <div className={cardGridClass()}>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-56 rounded-tile-sm" />
        ))}
      </div>
    </section>
  );
}

const CHIP =
  "inline-flex min-h-11 items-center gap-2 rounded-pill px-4 text-[0.875rem] transition-colors duration-(--duration-control)";

function ChapterList({ chapters }: { chapters: LibraryChapter[] }) {
  const t = useUiText();
  if (!chapters.length) return null;
  return (
    <section aria-labelledby="library-chapters-h" className="flex flex-col gap-3">
      <h2 id="library-chapters-h" className="m-0 font-mono text-label text-text-3 uppercase">
        {t("library.chapters_title")}
      </h2>
      <ul className="m-0 flex list-none flex-col rounded-tile-sm border border-line-soft bg-surface-1 p-0 px-5">
        {chapters.map((c) => (
          <li key={c.id} className="border-line-soft border-t first:border-t-0">
            <Link
              to="/learn/$chapterId"
              params={{ chapterId: c.id }}
              className="flex min-h-[60px] items-center gap-3 py-3 text-text no-underline hover:text-text"
            >
              <FileText aria-hidden="true" size={18} className="shrink-0 text-text-3" />
              <span className="min-w-0 flex-1 font-semibold [overflow-wrap:anywhere]">{c.title}</span>
              <span className="rounded-pill border border-line-chip px-2.5 py-[5px] font-mono text-[0.6875rem] text-text-2">
                {t("learn.your_file")}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * `/learn/library` (segment 15, Library board): bring in files, links and pasted text; answer the assistant's
 * placement proposals (violet confirm cards) and the Vault prompt; see each file's status, open what is ready,
 * undo a placement or delete. Polls every 2 s only while something is moving.
 */
export function LibraryPage({ generation }: { generation: boolean }) {
  const t = useUiText();
  const q = useLibrary();
  useUploadsVersion();
  const [filter, setFilter] = useState<Filter>("all");
  const [deleting, setDeleting] = useState<string | null>(null);
  const data = q.data;
  const groups = useMemo(() => (data ? confirmGroups(data.items, uploads) : []), [data]);
  const vault = useMemo(() => (data ? vaultPrompts(data.items, uploads) : []), [data]);

  if (q.isPending && !data) return <Loading />;
  if (!data) {
    return (
      <section aria-labelledby="library-h" className="flex flex-col gap-8 pt-6 tablet:pt-10">
        <Headline />
        <ErrorState
          title={t("library.load_failed")}
          message={t(requestErrorKey(q.error))}
          requestId={q.error instanceof ApiError ? q.error.requestId : null}
          onRetry={() => void q.refetch()}
          retrying={q.isFetching}
          retryLabel={t("library.retry")}
        />
      </section>
    );
  }

  const needsYou = vault.length + groups.length;
  const shown = data.items.filter((i) => matchesFilter(i, filter, uploads));
  const placedChapters = new Set(data.items.map((i) => i.placed?.chapter_id ?? i.chapter_id).filter(Boolean));
  const looseChapters = data.chapters.filter((c) => !placedChapters.has(c.id));
  const filters: Filter[] = generation ? [...FILTERS, "generated"] : [...FILTERS];

  return (
    <section
      aria-labelledby="library-h"
      className="flex flex-col gap-8 pt-6 pb-8 tablet:gap-10 tablet:pt-10"
      data-library-page=""
      data-library-idle={q.isFetching ? undefined : ""}
    >
      <Headline />

      <div className="flex flex-wrap gap-5">
        <Intake library={data} primary={needsYou === 0} />
        <Tile
          as="section"
          variant="assistant"
          aria-labelledby="library-about-h"
          className="flex min-w-0 flex-[1_1_320px] flex-col gap-4"
        >
          <TileLabel variant="assistant">{t("library.about_label")}</TileLabel>
          <p id="library-about-h" className="m-0 font-display text-[1.5rem] leading-[1.2]">
            {t("library.about_body")}
          </p>
          <p className="m-0 text-text-2 text-ui-sm">{t("library.about_private")}</p>
          <StorageMeter storage={data.storage} className="mt-auto" />
        </Tile>
      </div>

      {needsYou ? (
        <section aria-labelledby="library-needs-h" className="flex flex-col gap-4" data-needs-you="">
          <h2 id="library-needs-h" className="m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]">
            {t("library.needs_you_title")}
          </h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,420px),1fr))] gap-4">
            {vault.map((item, i) => (
              <VaultCard key={item.id} item={item} primary={i === 0} />
            ))}
            {groups.map((g, i) => (
              <ConfirmCard
                key={`${g.key}:${g.items.map((x) => x.id).join(",")}`}
                group={g}
                primary={vault.length === 0 && i === 0}
              />
            ))}
          </div>
        </section>
      ) : null}

      <AnkiTile />

      <section aria-labelledby="library-files-h" className="flex flex-col gap-5">
        <h2 id="library-files-h" className="sr-only">
          {t("library.files_title")}
        </h2>
        <div role="toolbar" aria-label={t("library.filters_label")} className="flex flex-wrap gap-2">
          {filters.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={cx(
                CHIP,
                filter === f
                  ? "bg-paper font-semibold text-ground"
                  : "border border-line-control text-text hover:bg-surface-2",
              )}
            >
              {f === "generated" ? t("generation.library_filter") : t(`library.filter.${f}`)}
            </button>
          ))}
        </div>

        {filter === "generated" ? (
          <Suspense fallback={<Skeleton className="h-40 w-full" />}>
            <GeneratedLibrary />
          </Suspense>
        ) : shown.length ? (
          <ul className={cardGridClass()} aria-label={t("library.files_title")}>
            {shown.map((item) => (
              <FileCard
                key={item.id}
                item={item}
                code={statusOf(item, uploads)}
                deleting={deleting === item.id}
                onDelete={() => setDeleting(item.id)}
                onCloseDelete={() => setDeleting(null)}
              />
            ))}
          </ul>
        ) : (
          <div
            className="flex flex-col items-start gap-2 rounded-tile-sm border border-dashed border-line-dashed p-6"
            data-library-empty=""
          >
            <Inbox aria-hidden="true" size={22} className="text-text-3" />
            <p className="m-0 font-semibold">
              {filter === "all" ? t("library.empty_title") : t("library.empty_filter_title")}
            </p>
            <p className="m-0 text-text-2">
              {filter === "all" ? t("library.empty_body") : t("library.empty_filter_body")}
            </p>
          </div>
        )}
      </section>

      {filter === "all" ? <ChapterList chapters={looseChapters} /> : null}
    </section>
  );
}
