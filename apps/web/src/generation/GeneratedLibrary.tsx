import { Button, ErrorState, Skeleton } from "@naadi/ui";
import { Check, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { errorKey, type GeneratedItem, useDeleteGenerated, useGeneratedList } from "./api";
import { useGenerationCopy } from "./copy";
import { GeneratedMedia } from "./GeneratedMedia";

type T = ReturnType<typeof useGenerationCopy>;

function DeleteDialog({ item, onClose }: { item: GeneratedItem | null; onClose: () => void }) {
  const t = useGenerationCopy();
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  const remove = useDeleteGenerated();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (item && !d.open) {
      remove.reset();
      d.showModal();
    } else if (!item && d.open) d.close();
  }, [item, remove.reset]);

  async function confirm() {
    if (!item || remove.isPending) return;
    try {
      await remove.mutateAsync(item.id);
      onClose();
    } catch {
      // shown below
    }
  }
  const kind = item ? t(item.kind === "image" ? "generation.kind_image" : "generation.kind_video") : "";
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      onClose={onClose}
      className="m-auto w-[min(100%-32px,480px)] rounded-tile-sm border border-line-strong bg-surface-1 p-0 text-text backdrop:bg-ground/70 backdrop:backdrop-blur-sm"
    >
      <div className="flex flex-col gap-4 p-6">
        <h2 id={`${id}-title`} className="m-0 font-display text-lead">
          {t("generation.library_delete_title", { kind })}
        </h2>
        <p className="m-0 text-text-2">{t("generation.library_delete_body")}</p>
        {remove.error ? (
          <p role="alert" className="m-0 text-amber text-ui-sm">
            {t(errorKey(remove.error))}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-3">
          <Button variant="secondary" onClick={onClose} disabled={remove.isPending}>
            {t("generation.library_delete_cancel")}
          </Button>
          <Button variant="strong" onClick={() => void confirm()} disabled={remove.isPending}>
            {t("generation.library_delete_confirm")}
          </Button>
        </div>
      </div>
    </dialog>
  );
}

function Card({ item, t, onDelete }: { item: GeneratedItem; t: T; onDelete: (i: GeneratedItem) => void }) {
  return (
    <li className="flex min-w-0 flex-col gap-2" data-testid="generated-card" data-id={item.id}>
      <GeneratedMedia item={item} compact />
      <div className="flex flex-wrap items-center justify-between gap-2">
        {item.published ? (
          <span className="inline-flex items-center gap-1.5 text-ui-sm text-text-2">
            <Check aria-hidden="true" size={14} />
            {t("generation.library_published")}
          </span>
        ) : (
          <span />
        )}
        <Button
          variant="secondary"
          icon={<Trash2 aria-hidden="true" size={16} />}
          onClick={() => onDelete(item)}
          aria-label={`${t("generation.library_delete")}: ${item.prompt}`}
        >
          {t("generation.library_delete")}
        </Button>
      </div>
    </li>
  );
}

function LibraryPage({
  before,
  isFirst,
  last,
  onMore,
  onDelete,
}: {
  before: string | null;
  isFirst: boolean;
  last: boolean;
  onMore: (cursor: string) => void;
  onDelete: (i: GeneratedItem) => void;
}) {
  const t = useGenerationCopy();
  const q = useGeneratedList(before);
  if (q.isPending) return <Skeleton className="h-48 w-full rounded-block" />;
  if (q.isError) {
    return (
      <ErrorState
        title={t("generation.library_error")}
        message={t(errorKey(q.error))}
        onRetry={() => void q.refetch()}
        retryLabel={t("generation.retry")}
      />
    );
  }
  const { items, next_before } = q.data;
  if (isFirst && items.length === 0) {
    return (
      <p className="m-0 text-text-2" data-testid="generated-empty">
        {t("generation.library_empty")}
      </p>
    );
  }
  return (
    <>
      <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 tablet:grid-cols-2 laptop:grid-cols-3">
        {items.map((i) => (
          <Card key={i.id} item={i} t={t} onDelete={onDelete} />
        ))}
      </ul>
      {next_before && last ? (
        <Button variant="secondary" className="self-start" onClick={() => onMore(next_before)}>
          {t("generation.library_more")}
        </Button>
      ) : null}
    </>
  );
}

/**
 * The Library's "Generated" filter (segment 15b): the student's own generated images and clips, labelled AI-generated,
 * with delete. S15 mounts it for the filter value `generated`; the filter chip text is `generation.library_filter`.
 */
export function GeneratedLibrary({ standalone = false }: { standalone?: boolean }) {
  const t = useGenerationCopy();
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [deleting, setDeleting] = useState<GeneratedItem | null>(null);
  return (
    <section aria-labelledby="generated-h" className="flex flex-col gap-4" data-testid="generated-library">
      {standalone ? (
        <h1 id="generated-h" className="m-0 font-display text-lead">
          {t("generation.library_title")}
        </h1>
      ) : (
        <h2 id="generated-h" className="m-0 font-display text-lead">
          {t("generation.library_title")}
        </h2>
      )}
      {cursors.map((c, i) => (
        <LibraryPage
          key={c ?? "first"}
          before={c}
          isFirst={i === 0}
          last={i === cursors.length - 1}
          onMore={(next) => setCursors((cur) => (cur.includes(next) ? cur : [...cur, next]))}
          onDelete={setDeleting}
        />
      ))}
      <DeleteDialog item={deleting} onClose={() => setDeleting(null)} />
    </section>
  );
}
