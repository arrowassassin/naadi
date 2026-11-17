import { Button, ErrorState, Skeleton, Tile } from "@naadi/ui";
import { X } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useMe } from "../auth/api";
import { useChapter, useCourses } from "../learn/api";
import { type AdminGeneratedItem, errorKey, useApproveGenerated, usePendingGenerated } from "./api";
import { useGenerationCopy } from "./copy";
import { GeneratedMedia } from "./GeneratedMedia";

const FIELD =
  "w-full rounded-block border border-line-control bg-surface-2 px-4 py-3 text-ui text-text placeholder:text-text-3 focus:border-text-3 focus:outline-none";
const LABEL = "font-mono text-label text-text-3 uppercase";

function SectionSelect({
  chapterId,
  value,
  onChange,
  id,
}: {
  chapterId: string;
  value: string;
  onChange: (v: string) => void;
  id: string;
}) {
  const t = useGenerationCopy();
  const q = useChapter(chapterId);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={FIELD} required>
      <option value="">{t("generation.admin_pick")}</option>
      {(q.data?.data.sections ?? []).map((s) => (
        <option key={s.id} value={s.id}>
          {s.heading}
        </option>
      ))}
    </select>
  );
}

function ApproveDialog({ item, onClose }: { item: AdminGeneratedItem | null; onClose: () => void }) {
  const t = useGenerationCopy();
  const ref = useRef<HTMLDialogElement>(null);
  const ids = useId();
  const approve = useApproveGenerated();
  const courses = useCourses({});
  const [chapter, setChapter] = useState("");
  const [section, setSection] = useState("");
  const [caption, setCaption] = useState("");

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (item && !d.open) {
      approve.reset();
      setChapter("");
      setSection("");
      setCaption("");
      d.showModal();
    } else if (!item && d.open) d.close();
  }, [item, approve.reset]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!item || !section || !caption.trim() || approve.isPending) return;
    try {
      await approve.mutateAsync({ id: item.id, body: { section_id: section, caption: caption.trim() } });
      onClose();
    } catch {
      // shown below
    }
  }
  const chapters = (courses.data?.data.chapters ?? []).filter((c) => c.kind === "chapter");
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${ids}-title`}
      onClose={onClose}
      className="m-auto w-[min(100%-32px,560px)] max-h-[calc(100dvh-32px)] overflow-y-auto rounded-tile-sm border border-line-strong bg-surface-1 p-0 text-text backdrop:bg-ground/70 backdrop:backdrop-blur-sm"
    >
      <form onSubmit={submit} className="flex flex-col gap-5 p-6 tablet:p-8">
        <div className="flex items-start justify-between gap-4">
          <h2 id={`${ids}-title`} className="m-0 font-display text-lead">
            {t("generation.admin_dialog_title")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("generation.admin_cancel")}
            className="flex size-11 shrink-0 items-center justify-center rounded-full border border-line-control bg-control text-text"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-chapter`} className={LABEL}>
            {t("generation.admin_chapter")}
          </label>
          <select
            id={`${ids}-chapter`}
            value={chapter}
            onChange={(e) => {
              setChapter(e.target.value);
              setSection("");
            }}
            className={FIELD}
            required
          >
            <option value="">{t("generation.admin_pick")}</option>
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </div>
        {chapter ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-section`} className={LABEL}>
              {t("generation.admin_section")}
            </label>
            <SectionSelect chapterId={chapter} value={section} onChange={setSection} id={`${ids}-section`} />
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-caption`} className={LABEL}>
            {t("generation.admin_caption")}
          </label>
          <input
            id={`${ids}-caption`}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            maxLength={300}
            aria-describedby={`${ids}-hint`}
            className={FIELD}
            required
          />
          <p id={`${ids}-hint`} className="m-0 text-text-3 text-ui-sm">
            {t("generation.admin_caption_hint")}
          </p>
        </div>
        {approve.error ? (
          <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text">
            {t(errorKey(approve.error))}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-3">
          <Button variant="secondary" onClick={onClose} disabled={approve.isPending}>
            {t("generation.admin_cancel")}
          </Button>
          <Button type="submit" variant="primary" disabled={!section || !caption.trim() || approve.isPending}>
            {t("generation.admin_confirm")}
          </Button>
        </div>
      </form>
    </dialog>
  );
}

/** `/admin/generated`: ready generated images waiting for approval. S19 later moves this list into its queues. */
export function AdminGenerated() {
  const t = useGenerationCopy();
  const me = useMe();
  const isAdmin = Boolean(me.data?.is_admin);
  const q = usePendingGenerated(isAdmin);
  const [picked, setPicked] = useState<AdminGeneratedItem | null>(null);

  if (!me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (!isAdmin) return null;
  return (
    <Tile as="section" aria-labelledby="gen-admin-h" className="flex flex-col gap-5" data-testid="admin-generated">
      <h1 id="gen-admin-h" className="m-0 font-display text-lead">
        {t("generation.admin_title")}
      </h1>
      {q.isPending ? <Skeleton className="h-48 w-full rounded-block" /> : null}
      {q.isError ? (
        <ErrorState
          title={t("generation.admin_error")}
          message={t(errorKey(q.error))}
          onRetry={() => void q.refetch()}
          retryLabel={t("generation.retry")}
        />
      ) : null}
      {q.data && q.data.items.length === 0 ? (
        <p className="m-0 text-text-2" data-testid="admin-generated-empty">
          {t("generation.admin_empty")}
        </p>
      ) : null}
      {q.data && q.data.items.length > 0 ? (
        <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 tablet:grid-cols-2 laptop:grid-cols-3">
          {q.data.items.map((i) => (
            <li key={i.id} className="flex flex-col gap-2" data-testid="admin-generated-card">
              <GeneratedMedia item={i} compact />
              <p className="m-0 font-mono text-label-sm text-text-3">
                {t("generation.admin_owner", { ref: i.user_ref })}
              </p>
              <Button variant="secondary" className="self-start" onClick={() => setPicked(i)}>
                {t("generation.admin_approve")}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      <ApproveDialog item={picked} onClose={() => setPicked(null)} />
    </Tile>
  );
}
