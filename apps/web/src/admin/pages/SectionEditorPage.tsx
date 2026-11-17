import type { EmbeddedQuestion, MediaOut } from "@naadi/shared";
import type { ContentDoc } from "@naadi/shared/content";
import { Button, Chip, cx } from "@naadi/ui";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { EditorContent, useEditor } from "@tiptap/react";
import { Check, CircleAlert, Eye, LoaderCircle, PenLine, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ContentRenderer } from "../../content/ContentRenderer";
import {
  type AdminMedia,
  type AdminQuestionSummary,
  api,
  qk,
  type Revision,
  type SectionEditor,
  type SourceRow,
} from "../api";
import { useSources } from "../components/pickers";
import { AdminPage, Dialog, Failure, fmtDateTime, InlineError, LABEL, Loading, TEXTAREA, useT } from "../components/ui";
import { type EditorEnv, EditorEnvContext } from "../editor/AtomNodeView";
import { editorExtensions, insertBlock } from "../editor/extensions";
import { History } from "../editor/History";
import { NodeDialog, type NodeDialogState } from "../editor/NodeDialog";
import { Toolbar } from "../editor/Toolbar";
import { type SaveState, useAutosave } from "../editor/useAutosave";

const EMPTY_DOC: ContentDoc = { type: "doc", content: [{ type: "paragraph" }] };

function isDoc(v: unknown): v is ContentDoc {
  return typeof v === "object" && v !== null && (v as { type?: unknown }).type === "doc";
}

type AnyMedia = AdminMedia;

/** A media row (editor payload or library) → the reader's MediaOut, so the preview renders what a student sees. */
export function toMediaOut(m: AnyMedia, sources: ReadonlyMap<string, SourceRow>): MediaOut {
  const s = sources.get(m.source_id);
  return {
    id: m.id,
    kind: m.kind as MediaOut["kind"],
    title: m.title,
    caption: m.caption,
    attribution: m.attribution,
    url: m.preview_url,
    width: m.width,
    height: m.height,
    youtube_id: m.youtube_id,
    embeddable: m.embeddable,
    duration_s: m.duration_s,
    source: s ? { id: s.id, name: s.name, license: s.license, url: null } : null,
  };
}

/** Every questionEmbed id in a document. */
export function questionIdsOf(doc: unknown): string[] {
  const out = new Set<string>();
  const walk = (n: unknown) => {
    if (!n || typeof n !== "object") return;
    const node = n as { type?: string; attrs?: { questionId?: unknown }; content?: unknown[] };
    if (node.type === "questionEmbed" && typeof node.attrs?.questionId === "string") out.add(node.attrs.questionId);
    for (const c of node.content ?? []) walk(c);
  };
  walk(doc);
  return [...out].sort();
}

function SaveStatus({ state, draftAt, onRetry }: { state: SaveState; draftAt: string | null; onRetry: () => void }) {
  const t = useT();
  let icon = <Check aria-hidden="true" size={16} />;
  let text: string;
  switch (state.kind) {
    case "clean":
      text = draftAt ? t("admin.editor.save.draft_at", { time: fmtDateTime(draftAt) }) : t("admin.editor.save.clean");
      break;
    case "dirty":
      icon = <PenLine aria-hidden="true" size={16} />;
      text = t("admin.editor.save.dirty");
      break;
    case "saving":
      icon = <LoaderCircle aria-hidden="true" size={16} className="animate-spin" />;
      text = t("admin.editor.save.saving");
      break;
    case "saved":
      text = t("admin.editor.save.saved", { time: fmtDateTime(state.at) });
      break;
    case "invalid":
      icon = <CircleAlert aria-hidden="true" size={16} className="text-amber" />;
      text = t("admin.editor.save.invalid", { path: state.path });
      break;
    case "conflict":
      icon = <CircleAlert aria-hidden="true" size={16} className="text-amber" />;
      text = t("admin.editor.save.conflict");
      break;
    case "error":
      icon = <CircleAlert aria-hidden="true" size={16} className="text-amber" />;
      text = t("admin.editor.save.error");
      break;
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span role="status" data-save-state={state.kind} className="inline-flex items-center gap-2 text-text-2">
        {icon}
        {text}
      </span>
      {state.kind === "error" ? (
        <Button onClick={onRetry} className="min-h-11">
          {t("admin.action.retry")}
        </Button>
      ) : null}
    </span>
  );
}

function PublishDialog({
  open,
  onClose,
  onPublish,
  busy,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onPublish: (note: string | null) => void;
  busy: boolean;
  error: unknown;
}) {
  const t = useT();
  const id = useId();
  const [note, setNote] = useState("");
  useEffect(() => {
    if (open) setNote("");
  }, [open]);
  return (
    <Dialog open={open} onClose={onClose} title={t("admin.editor.publish_title")}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) onPublish(note.trim() || null);
        }}
      >
        <p className="m-0 text-text-2">{t("admin.editor.publish_body")}</p>
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
        <InlineError error={error} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" disabled={busy} icon={<Send size={16} />}>
            {t(busy ? "admin.editor.publishing" : "admin.editor.publish_confirm")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function Workspace({ data, sectionId }: { data: SectionEditor; sectionId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const sources = useSources();
  const autosave = useAutosave(sectionId, data.draft, data.live.revision_id);
  const initial = isDoc(data.draft?.body) ? data.draft.body : isDoc(data.live.body) ? data.live.body : EMPTY_DOC;
  const [doc, setDoc] = useState<ContentDoc>(initial);
  const [pane, setPane] = useState<"edit" | "preview">("edit");
  const [dialog, setDialog] = useState<NodeDialogState | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [picked, setPicked] = useState<Map<string, AdminMedia>>(new Map());
  const [pickedQ, setPickedQ] = useState<Map<string, AdminQuestionSummary>>(new Map());
  const [draftAt, setDraftAt] = useState<string | null>(data.draft?.updated_at ?? null);
  const changeRef = useRef(autosave.change);
  changeRef.current = autosave.change;
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const editor = useEditor(
    {
      extensions: editorExtensions(),
      content: initial,
      // The production CSP allows no inline <style>: the editor's base CSS is in admin.css.
      injectCSS: false,
      editorProps: {
        attributes: {
          id: `${ids}-ed`,
          role: "textbox",
          "aria-multiline": "true",
          "aria-label": t("admin.editor.body_label"),
          "data-admin-editor": "",
          class: "admin-prose min-h-[24rem] rounded-block border border-line-soft bg-surface-1 p-4 tablet:p-6",
        },
      },
      onUpdate: ({ editor: e }) => {
        const next = e.getJSON() as ContentDoc;
        changeRef.current(next);
        if (previewTimer.current) clearTimeout(previewTimer.current);
        previewTimer.current = setTimeout(() => setDoc(next), 250);
      },
    },
    [],
  );
  useEffect(
    () => () => {
      if (previewTimer.current) clearTimeout(previewTimer.current);
    },
    [],
  );

  const replaceContent = useCallback(
    (body: unknown) => {
      const next = isDoc(body) ? body : EMPTY_DOC;
      editor?.commands.setContent(next, { emitUpdate: false });
      setDoc(next);
    },
    [editor],
  );

  // Lookups for atom cards and the preview.
  const mediaById = useMemo(() => {
    const m = new Map<string, AnyMedia>(data.media.map((x) => [x.id, x]));
    for (const [k, v] of picked) m.set(k, v);
    return m;
  }, [data.media, picked]);
  const questionsById = useMemo(() => {
    const m = new Map<string, { id: string; stem_text?: string }>(data.questions.map((x) => [x.id, x]));
    for (const [k, v] of pickedQ) m.set(k, v);
    return m;
  }, [data.questions, pickedQ]);
  const sourceMap = useMemo(() => new Map((sources.data ?? []).map((s) => [s.id, s])), [sources.data]);
  const mediaOut = useMemo(() => {
    const out: Record<string, MediaOut> = {};
    for (const [k, v] of mediaById) out[k] = toMediaOut(v, sourceMap);
    return out;
  }, [mediaById, sourceMap]);
  const qids = useMemo(() => questionIdsOf(doc), [doc]);
  const qFull = useQueries({
    queries: qids.map((id) => ({
      queryKey: qk.question(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => api.question(id, signal),
      staleTime: 60_000,
    })),
  });
  const questionsOut = useMemo(() => {
    const out: Record<string, EmbeddedQuestion> = {};
    for (const q of qFull) {
      if (!q.data) continue;
      // Stem and options only, exactly what the student's embed shows (never the key or explanation).
      out[q.data.id] = {
        id: q.data.id,
        stem_html: q.data.stem_html,
        options: q.data.options.map((o) => ({ label: o.label, html: o.html })),
      };
    }
    return out;
  }, [qFull]);

  const env: EditorEnv = useMemo(
    () => ({
      media: mediaById,
      questions: questionsById,
      editNode: (type, attrs, pos) => setDialog({ type, attrs, pos }),
    }),
    [mediaById, questionsById],
  );

  function applyNode(s: NodeDialogState) {
    if (!editor) return;
    if (s.pos === null) {
      insertBlock(editor, { type: s.type, attrs: s.attrs });
    } else {
      const pos = s.pos;
      editor
        .chain()
        .focus()
        .command(({ tr }) => {
          tr.setNodeMarkup(pos, undefined, s.attrs);
          return true;
        })
        .run();
    }
    setDialog(null);
  }

  const DEFAULTS: Record<string, Record<string, unknown>> = {
    figure: { mediaId: null },
    video: { mediaId: null, startS: null, endS: null },
    pdf: { mediaId: null, page: null },
    questionEmbed: { questionId: null },
    mermaid: { code: "" },
    math: { latex: "" },
  };

  const invalidateAll = () => {
    void qc.invalidateQueries({ queryKey: qk.editor(sectionId) });
    void qc.invalidateQueries({ queryKey: qk.revisions(sectionId) });
    void qc.invalidateQueries({ queryKey: qk.chapter(data.chapter.id) });
  };

  const publish = useMutation({
    mutationFn: async (note: string | null) => {
      const d = await autosave.flush();
      if (!d) throw new Error("nothing to publish");
      return api.publish(sectionId, { revision_id: d.id, expected_updated_at: d.updated_at, note });
    },
    onSuccess: (rev) => {
      autosave.reset(null, { state: { kind: "clean" } });
      setDraftAt(null);
      setPublishing(false);
      qc.setQueryData(qk.editor(sectionId), {
        ...data,
        live: { body: rev.body, revision_id: rev.id },
        draft: null,
        section: { ...data.section, has_draft: false, live_revision_id: rev.id },
      });
      invalidateAll();
    },
  });

  const discard = useMutation({
    mutationFn: () => api.discardDraft(sectionId),
    onSuccess: () => {
      autosave.reset(null);
      setDraftAt(null);
      replaceContent(data.live.body);
      invalidateAll();
    },
  });

  async function resolveConflict(keepMine: boolean) {
    const fresh = await qc.fetchQuery({
      queryKey: qk.editor(sectionId),
      queryFn: ({ signal }) => api.sectionEditor(sectionId, signal),
      staleTime: 0,
    });
    if (keepMine) {
      autosave.reset(fresh.draft, { keepPending: true });
    } else {
      autosave.reset(fresh.draft);
      replaceContent(fresh.draft?.body ?? fresh.live.body);
    }
    setDraftAt(fresh.draft?.updated_at ?? null);
  }

  function onRolledBack(rev: Revision) {
    autosave.reset(rev.status === "draft" ? rev : null, {
      state: rev.status === "draft" ? { kind: "saved", at: rev.updated_at } : { kind: "clean" },
    });
    setDraftAt(rev.status === "draft" ? rev.updated_at : null);
    replaceContent(rev.body);
    invalidateAll();
  }

  const state = autosave.state;
  const hasDraft = state.kind === "saved" || state.kind === "dirty" || state.kind === "saving" || draftAt !== null;
  const canPublish =
    hasDraft && state.kind !== "conflict" && state.kind !== "invalid" && !publish.isPending && editor !== null;

  return (
    <AdminPage
      label={data.chapter.title}
      title={data.section.heading}
      actions={
        <Button
          variant="primary"
          size="cta"
          icon={<Send size={18} />}
          disabled={!canPublish}
          aria-describedby={canPublish ? undefined : `${ids}-why`}
          onClick={() => {
            publish.reset();
            setPublishing(true);
          }}
        >
          {t("admin.editor.publish")}
        </Button>
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/admin/chapters/$chapterId" params={{ chapterId: data.chapter.id }} className="min-h-11 text-text-2">
          {t("admin.editor.back")}
        </Link>
        <Chip tone="meta">{data.section.id}</Chip>
        {data.live.revision_id ? (
          <Chip tone="done" icon={<Check size={13} />}>
            {t("admin.editor.live_exists")}
          </Chip>
        ) : (
          <Chip tone="planned">{t("admin.sections.not_live")}</Chip>
        )}
        <SaveStatus state={state} draftAt={draftAt} onRetry={() => void autosave.flush()} />
        {canPublish ? null : (
          <span id={`${ids}-why`} className="text-ui-sm text-text-3">
            {t(state.kind === "conflict" ? "admin.editor.publish_blocked_conflict" : "admin.editor.publish_nothing")}
          </span>
        )}
        {draftAt || state.kind === "saved" ? (
          <Button
            icon={<Trash2 size={16} />}
            disabled={discard.isPending}
            onClick={() => {
              if (window.confirm(t("admin.editor.discard_confirm"))) discard.mutate();
            }}
          >
            {t("admin.editor.discard")}
          </Button>
        ) : null}
      </div>
      {state.kind === "conflict" ? (
        <div role="alert" className="flex flex-col gap-3 rounded-block border border-amber/60 bg-surface-2 p-4">
          <p className="m-0 font-semibold text-text">{t("admin.editor.conflict_title")}</p>
          <p className="m-0 text-text-2">{t("admin.editor.conflict_body")}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="strong" onClick={() => void resolveConflict(false)}>
              {t("admin.editor.conflict_reload")}
            </Button>
            <Button onClick={() => void resolveConflict(true)}>{t("admin.editor.conflict_keep")}</Button>
          </div>
        </div>
      ) : null}
      {state.kind === "error" ? <InlineError error={state.error} /> : null}
      {publish.error && !publishing ? <InlineError error={publish.error} /> : null}
      {discard.error ? <InlineError error={discard.error} /> : null}

      <fieldset className="m-0 flex gap-2 border-0 p-0 laptop:hidden">
        <legend className="sr-only">{t("admin.editor.view_label")}</legend>
        <Button aria-pressed={pane === "edit"} icon={<PenLine size={16} />} onClick={() => setPane("edit")}>
          {t("admin.editor.view_edit")}
        </Button>
        <Button aria-pressed={pane === "preview"} icon={<Eye size={16} />} onClick={() => setPane("preview")}>
          {t("admin.editor.view_preview")}
        </Button>
      </fieldset>

      <div className="grid min-w-0 items-start gap-5 laptop:grid-cols-2">
        <div className={cx("min-w-0 flex-col gap-3", pane === "edit" ? "flex" : "hidden laptop:flex")}>
          <EditorEnvContext.Provider value={env}>
            <div className="sticky top-[calc(var(--shell-top,0px)+8px)] z-10">
              <Toolbar
                editor={editor}
                controls={`${ids}-ed`}
                onInsertAtom={(type) => setDialog({ type, attrs: DEFAULTS[type] ?? {}, pos: null })}
              />
            </div>
            <EditorContent editor={editor} />
          </EditorEnvContext.Provider>
        </div>
        <section
          aria-labelledby={`${ids}-pv`}
          className={cx("min-w-0 flex-col gap-3", pane === "preview" ? "flex" : "hidden laptop:flex")}
        >
          <h2 id={`${ids}-pv`} className="m-0 font-mono text-label text-text-3 uppercase">
            {t("admin.editor.preview_label")}
          </h2>
          <div data-admin-preview="" className="rounded-block border border-line-soft p-4 tablet:p-6">
            <ContentRenderer
              doc={doc}
              sectionId={sectionId}
              media={mediaOut}
              questions={questionsOut}
              mode="dev"
              text={t}
            />
          </div>
        </section>
      </div>

      <History
        sectionId={sectionId}
        liveRevisionId={data.live.revision_id}
        onRolledBack={onRolledBack}
        confirmDiscard={() => !autosave.hasUnsaved() || window.confirm(t("admin.editor.rollback_confirm"))}
      />

      <NodeDialog
        state={dialog}
        exam={data.chapter.exam}
        onClose={() => setDialog(null)}
        onApply={applyNode}
        onMedia={(m) => setPicked((p) => new Map(p).set(m.id, m))}
        onQuestion={(q) => setPickedQ((p) => new Map(p).set(q.id, q))}
      />
      <PublishDialog
        open={publishing}
        onClose={() => setPublishing(false)}
        onPublish={(note) => publish.mutate(note)}
        busy={publish.isPending}
        error={publish.error}
      />
    </AdminPage>
  );
}

/** `/admin/sections/$sectionId/edit`: the chapter editor for one section. */
export function SectionEditorPage({ sectionId }: { sectionId: string }) {
  const t = useT();
  const q = useQuery({
    queryKey: qk.editor(sectionId),
    queryFn: ({ signal }) => api.sectionEditor(sectionId, signal),
    // The editor owns the document once open: refetching must not replace what is being typed.
    refetchOnWindowFocus: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  if (q.isPending) return <Loading rows={3} tall />;
  if (q.isError) {
    return (
      <AdminPage label={t("admin.chapters.title")} title={t("admin.editor.title")}>
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </AdminPage>
    );
  }
  // Later cache updates (publish, rollback) re-render the same workspace; the editor itself is created once.
  return <Workspace data={q.data} sectionId={sectionId} />;
}
