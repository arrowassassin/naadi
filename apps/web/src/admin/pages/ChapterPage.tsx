import { Button, Chip, cx, Tile, TileLabel } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, Check, FilePenLine, PenLine, Plus } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";
import { type AdminChapter, type AdminSection, api, type ChapterStatus, isConflict, qk } from "../api";
import { SourceSelect, TaxonomyPicker } from "../components/pickers";
import {
  AdminPage,
  Dialog,
  Empty,
  Failure,
  fmtDateTime,
  INPUT,
  InlineError,
  LABEL,
  Loading,
  StatusChip,
  TEXTAREA,
  useT,
} from "../components/ui";

/** The status workflow: which moves each status allows; the first is the view's next action (lime). */
export const CHAPTER_FLOW: Record<ChapterStatus, ChapterStatus[]> = {
  draft: ["in_review"],
  in_review: ["published", "draft"],
  published: ["retired", "in_review"],
  retired: ["draft"],
};

export const MOVE_KEYS: Record<ChapterStatus, string> = {
  draft: "admin.workflow.to_draft",
  in_review: "admin.workflow.to_review",
  published: "admin.workflow.to_published",
  retired: "admin.workflow.to_retired",
};

function StatusDialog({
  chapter,
  target,
  onClose,
}: {
  chapter: AdminChapter;
  target: ChapterStatus | null;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const id = useId();
  const [note, setNote] = useState("");
  useEffect(() => {
    if (target) setNote("");
  }, [target]);
  const move = useMutation({
    mutationFn: (status: ChapterStatus) => api.chapterStatus(chapter.id, { status, note: note.trim() || null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.all });
      onClose();
    },
  });
  return (
    <Dialog open={target !== null} onClose={onClose} title={target ? t(MOVE_KEYS[target]) : ""}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (target && !move.isPending) move.mutate(target);
        }}
      >
        <p className="m-0 text-text-2">{t("admin.workflow.confirm", { title: chapter.title })}</p>
        <label htmlFor={id} className={LABEL}>
          {t("admin.field.note_optional")}
          <textarea
            id={id}
            className={TEXTAREA}
            maxLength={1000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <InlineError error={move.error} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" disabled={move.isPending}>
            {t(move.isPending ? "admin.action.saving" : "admin.action.confirm")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function MetadataForm({ chapter }: { chapter: AdminChapter }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const [title, setTitle] = useState(chapter.title);
  const [meta, setMeta] = useState(chapter.meta ?? "");
  const [source, setSource] = useState(chapter.source_id ?? "");
  const [saved, setSaved] = useState(false);
  const dirty =
    title.trim() !== chapter.title || (meta.trim() || null) !== chapter.meta || source !== chapter.source_id;
  const save = useMutation({
    mutationFn: () =>
      api.patchChapter(chapter.id, {
        title: title.trim(),
        meta: meta.trim() || null,
        source_id: source,
        expected_updated_at: chapter.updated_at,
      }),
    onSuccess: () => {
      setSaved(true);
      void qc.invalidateQueries({ queryKey: qk.all });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!dirty || !title.trim() || !source || save.isPending) return;
    setSaved(false);
    save.mutate();
  }
  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <label htmlFor={`${ids}-t`} className={LABEL}>
        {t("admin.field.title")}
        <input
          id={`${ids}-t`}
          className={INPUT}
          value={title}
          maxLength={200}
          aria-invalid={!title.trim() || undefined}
          onChange={(e) => {
            setTitle(e.target.value);
            setSaved(false);
          }}
        />
      </label>
      <label htmlFor={`${ids}-m`} className={LABEL}>
        {t("admin.field.meta")}
        <input
          id={`${ids}-m`}
          className={INPUT}
          value={meta}
          maxLength={200}
          onChange={(e) => {
            setMeta(e.target.value);
            setSaved(false);
          }}
        />
      </label>
      <SourceSelect
        use="text"
        value={source}
        onChange={(v) => {
          setSource(v);
          setSaved(false);
        }}
        invalid={!source}
      />
      <InlineError error={save.error}>
        {isConflict(save.error) ? <p className="m-0 text-text-2">{t("admin.conflict.reload_hint")}</p> : null}
      </InlineError>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="strong" disabled={!dirty || !title.trim() || !source || save.isPending}>
          {t(save.isPending ? "admin.action.saving" : "admin.action.save")}
        </Button>
        {saved && !dirty ? (
          <span role="status" className="inline-flex items-center gap-2 text-text-2">
            <Check aria-hidden="true" size={16} />
            {t("admin.state.saved")}
          </span>
        ) : null}
      </div>
    </form>
  );
}

function SectionDialog({
  chapter,
  section,
  open,
  onClose,
}: {
  chapter: AdminChapter;
  section: AdminSection | null;
  open: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const id = useId();
  const [heading, setHeading] = useState("");
  const [tax, setTax] = useState<string[]>([]);
  useEffect(() => {
    if (open) {
      setHeading(section?.heading ?? "");
      setTax(section?.taxonomy_ids ?? []);
    }
  }, [open, section]);
  const save = useMutation({
    mutationFn: () =>
      section
        ? api.patchSection(section.id, { heading: heading.trim(), taxonomy_ids: tax })
        : api.createSection(chapter.id, { heading: heading.trim(), taxonomy_ids: tax }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.chapter(chapter.id) });
      void qc.invalidateQueries({ queryKey: ["admin", "chapters"] });
      onClose();
    },
  });
  return (
    <Dialog open={open} onClose={onClose} title={t(section ? "admin.sections.edit_title" : "admin.sections.new_title")}>
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (heading.trim() && !save.isPending) save.mutate();
        }}
      >
        <label htmlFor={id} className={LABEL}>
          {t("admin.field.heading")}
          <input
            id={id}
            className={INPUT}
            value={heading}
            maxLength={200}
            onChange={(e) => setHeading(e.target.value)}
          />
        </label>
        <TaxonomyPicker exam={chapter.exam} value={tax} onChange={setTax} />
        <InlineError error={save.error} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" disabled={!heading.trim() || save.isPending}>
            {t(save.isPending ? "admin.action.saving" : section ? "admin.action.save" : "admin.sections.create")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

/** `/admin/chapters/$chapterId`: metadata, the status workflow and the sections (order, headings, editor links). */
export function ChapterPage({ chapterId }: { chapterId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: qk.chapter(chapterId), queryFn: ({ signal }) => api.chapter(chapterId, signal) });
  const [target, setTarget] = useState<ChapterStatus | null>(null);
  const [editing, setEditing] = useState<AdminSection | null>(null);
  const [adding, setAdding] = useState(false);
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.reorderSections(chapterId, ids),
    onSettled: () => void qc.invalidateQueries({ queryKey: qk.chapter(chapterId) }),
  });

  if (q.isPending) return <Loading rows={3} tall />;
  if (q.isError) {
    return (
      <AdminPage label={t("admin.chapters.title")} title={t("admin.chapters.chapter")}>
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </AdminPage>
    );
  }
  const { chapter, sections } = q.data;
  const moves = CHAPTER_FLOW[chapter.status] ?? [];
  const sorted = [...sections].sort((a, b) => a.sort - b.sort);
  function move(i: number, d: -1 | 1) {
    const next = [...sorted];
    const [s] = next.splice(i, 1);
    if (!s) return;
    next.splice(i + d, 0, s);
    qc.setQueryData(qk.chapter(chapterId), {
      chapter,
      sections: next.map((x, n) => ({ ...x, sort: n + 1 })),
    });
    reorder.mutate(next.map((x) => x.id));
  }

  return (
    <AdminPage
      label={t("admin.chapters.chapter")}
      title={chapter.title}
      actions={moves.map((m, i) => (
        <Button key={m} variant={i === 0 ? "primary" : "secondary"} size="cta" onClick={() => setTarget(m)}>
          {t(MOVE_KEYS[m])}
        </Button>
      ))}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={chapter.status} prefix={t("admin.field.status")} />
        <Chip tone="meta">{chapter.id}</Chip>
        <Link to="/admin/chapters" search={{ exam: chapter.exam }} className="ml-auto min-h-11 text-text-2">
          {t("admin.chapters.back")}
        </Link>
      </div>
      <div className="grid items-start gap-5 laptop:grid-cols-[3fr_2fr]">
        <Tile as="section" aria-labelledby="sections-h" className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="sections-h" className="m-0 font-display font-bold text-[1.5rem]">
              {t("admin.sections.title")}
            </h2>
            <Button icon={<Plus size={16} />} onClick={() => setAdding(true)}>
              {t("admin.sections.new")}
            </Button>
          </div>
          {reorder.error ? <InlineError error={reorder.error} /> : null}
          {sorted.length === 0 ? (
            <Empty title={t("admin.sections.empty")} body={t("admin.sections.empty_body")} />
          ) : (
            <ol className="m-0 flex list-none flex-col p-0" aria-busy={reorder.isPending}>
              {sorted.map((s, i) => (
                <li
                  key={s.id}
                  className="flex flex-col gap-3 border-line-soft border-t py-4 first:border-t-0 tablet:flex-row tablet:items-center"
                >
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="font-semibold text-text">{s.heading}</span>
                    <span className="flex flex-wrap items-center gap-2 text-ui-sm text-text-2">
                      <span className="font-mono text-label-sm text-text-3">{s.id}</span>
                      {s.has_draft ? (
                        <Chip tone="warning" icon={<FilePenLine size={13} />}>
                          {t("admin.sections.has_draft", { time: fmtDateTime(s.draft_updated_at) })}
                        </Chip>
                      ) : null}
                      {s.live_revision_id ? null : <Chip tone="planned">{t("admin.sections.not_live")}</Chip>}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Link
                      to="/admin/sections/$sectionId/edit"
                      params={{ sectionId: s.id }}
                      className={cx(
                        "inline-flex min-h-12 items-center gap-2 rounded-pill border border-line-control px-5 text-ui-sm text-text no-underline hover:bg-surface-2 hover:text-text",
                      )}
                    >
                      <PenLine aria-hidden="true" size={16} />
                      {t("admin.sections.edit_body")}
                      <span className="sr-only">{s.heading}</span>
                    </Link>
                    <Button
                      aria-label={t("admin.sections.edit_meta", { name: s.heading })}
                      onClick={() => setEditing(s)}
                    >
                      {t("admin.sections.rename")}
                    </Button>
                    <Button
                      aria-label={t("admin.action.move_up", { name: s.heading })}
                      disabled={i === 0 || reorder.isPending}
                      onClick={() => move(i, -1)}
                      icon={<ArrowUp size={16} />}
                    />
                    <Button
                      aria-label={t("admin.action.move_down", { name: s.heading })}
                      disabled={i === sorted.length - 1 || reorder.isPending}
                      onClick={() => move(i, 1)}
                      icon={<ArrowDown size={16} />}
                    />
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Tile>
        <Tile as="section" aria-labelledby="meta-h" className="flex min-w-0 flex-col gap-4">
          <TileLabel>{t("admin.chapters.details")}</TileLabel>
          <h2 id="meta-h" className="sr-only">
            {t("admin.chapters.details")}
          </h2>
          <MetadataForm key={chapter.updated_at} chapter={chapter} />
        </Tile>
      </div>
      <StatusDialog chapter={chapter} target={target} onClose={() => setTarget(null)} />
      <SectionDialog
        chapter={chapter}
        section={editing}
        open={adding || editing !== null}
        onClose={() => {
          setAdding(false);
          setEditing(null);
        }}
      />
    </AdminPage>
  );
}
