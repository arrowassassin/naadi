import { Button, Chip, cx } from "@naadi/ui";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Ban, Check, CirclePlay, Link2, Search, Unlink, Upload, X } from "lucide-react";
import { type DragEvent, type FormEvent, useEffect, useId, useRef, useState } from "react";
import { type AdminMedia, api, isConflict, MEDIA_KINDS, qk, REVIEW_STATUSES, type YoutubeLookup } from "../api";
import { SourceSelect, type SourceUse, TaxonomyPicker } from "../components/pickers";
import { type SectionChoice, SectionPicker } from "../components/SectionPicker";
import {
  AdminPage,
  Dialog,
  Empty,
  Failure,
  FilterPills,
  fmtDate,
  fmtSeconds,
  INPUT,
  InlineError,
  LABEL,
  Loading,
  MoreButton,
  STATUS_KEYS,
  StatusChip,
  TEXTAREA,
  useT,
} from "../components/ui";
import { MEDIA_KIND_KEYS, MediaThumb } from "../editor/pickers";
import type { MediaSearch } from "../routes";

export const FIGURE_TYPES = ["image/svg+xml", "image/png", "image/jpeg", "image/webp", "image/gif"] as const;
export const PDF_TYPES = ["application/pdf"] as const;
const ACCEPT = [...FIGURE_TYPES, ...PDF_TYPES].join(",");

/** Which upload endpoint a file goes to, or null when the type is not accepted. */
export function uploadKind(file: { type: string; name: string }): "figure" | "pdf" | null {
  const type = file.type || (file.name.toLowerCase().endsWith(".svg") ? "image/svg+xml" : "");
  if ((FIGURE_TYPES as readonly string[]).includes(type)) return "figure";
  if ((PDF_TYPES as readonly string[]).includes(type) || file.name.toLowerCase().endsWith(".pdf")) return "pdf";
  return null;
}

function EmbeddableWord({ value }: { value: boolean | null }) {
  const t = useT();
  if (value === null) return <Chip tone="meta">{t("admin.media.embeddable_unknown")}</Chip>;
  return value ? (
    <Chip tone="done" icon={<Check size={13} />}>
      {t("admin.media.embeddable_yes")}
    </Chip>
  ) : (
    <Chip tone="warning" icon={<Ban size={13} />}>
      {t("admin.media.embeddable_no")}
    </Chip>
  );
}

interface MetaFields {
  title: string;
  caption: string;
  attribution: string;
  source_id: string;
  taxonomy_ids: string[];
}
const EMPTY_META: MetaFields = { title: "", caption: "", attribution: "", source_id: "", taxonomy_ids: [] };

function MetaForm({
  value,
  onChange,
  tried,
  use,
  titleRequired = true,
}: {
  use: SourceUse;
  value: MetaFields;
  onChange: (v: MetaFields) => void;
  tried: boolean;
  titleRequired?: boolean;
}) {
  const t = useT();
  const ids = useId();
  return (
    <div className="flex flex-col gap-5">
      <label htmlFor={`${ids}-t`} className={LABEL}>
        {t(titleRequired ? "admin.field.title" : "admin.field.title_optional")}
        <input
          id={`${ids}-t`}
          className={INPUT}
          value={value.title}
          maxLength={300}
          aria-invalid={(tried && titleRequired && !value.title.trim()) || undefined}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
        />
      </label>
      <label htmlFor={`${ids}-c`} className={LABEL}>
        {t("admin.field.caption")}
        <textarea
          id={`${ids}-c`}
          className={TEXTAREA}
          value={value.caption}
          maxLength={1000}
          onChange={(e) => onChange({ ...value, caption: e.target.value })}
        />
      </label>
      <label htmlFor={`${ids}-a`} className={LABEL}>
        {t("admin.field.attribution")}
        <input
          id={`${ids}-a`}
          className={INPUT}
          value={value.attribution}
          maxLength={500}
          onChange={(e) => onChange({ ...value, attribution: e.target.value })}
        />
      </label>
      <SourceSelect
        use={use}
        value={value.source_id}
        onChange={(source_id) => onChange({ ...value, source_id })}
        invalid={tried && !value.source_id}
      />
      {tried && !value.source_id ? (
        <span className="text-ui-sm text-amber">{t("admin.field.source_required")}</span>
      ) : null}
      <TaxonomyPicker
        exam={undefined}
        value={value.taxonomy_ids}
        onChange={(ids2) => onChange({ ...value, taxonomy_ids: ids2 })}
      />
    </div>
  );
}

function UploadDialog({ file, onClose }: { file: File | null; onClose: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [meta, setMeta] = useState<MetaFields>(EMPTY_META);
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (file) {
      setMeta({ ...EMPTY_META, title: file.name.replace(/\.[a-z0-9]+$/i, "") });
      setTried(false);
    }
  }, [file]);
  const kind = file ? uploadKind(file) : null;
  const upload = useMutation({
    mutationFn: () => {
      if (!file || !kind) throw new Error("no file");
      // The server takes the raw file; an SVG picked from a filesystem without a MIME type is labelled here.
      const body = file.type ? file : new Blob([file], { type: kind === "pdf" ? "application/pdf" : "image/svg+xml" });
      return api.uploadMedia(body, {
        source_id: meta.source_id,
        title: meta.title.trim(),
        caption: meta.caption.trim() || null,
        attribution: meta.attribution.trim() || null,
        taxonomy_ids: meta.taxonomy_ids,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "media"] });
      onClose();
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (!meta.title.trim() || !meta.source_id || upload.isPending) return;
    upload.mutate();
  }
  return (
    <Dialog
      open={file !== null}
      onClose={onClose}
      title={t(kind === "pdf" ? "admin.media.upload_pdf_title" : "admin.media.upload_figure_title")}
    >
      <form noValidate onSubmit={submit} className="flex flex-col gap-5">
        <p className="m-0 break-all font-mono text-ui-sm text-text-2">{file?.name}</p>
        <MetaForm value={meta} onChange={setMeta} tried={tried} use="host" />
        <InlineError error={upload.error} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong" icon={<Upload size={16} />} disabled={upload.isPending}>
            {t(upload.isPending ? "admin.media.uploading" : "admin.media.upload")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function YoutubeDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const [url, setUrl] = useState("");
  const [found, setFound] = useState<YoutubeLookup | null>(null);
  const [meta, setMeta] = useState<MetaFields>(EMPTY_META);
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (open) {
      setUrl("");
      setFound(null);
      setMeta(EMPTY_META);
      setTried(false);
    }
  }, [open]);
  const lookup = useMutation({
    mutationFn: () => api.youtubeLookup(url.trim()),
    onSuccess: (r) => {
      setFound(r);
      setMeta((m) => ({ ...m, title: m.title || r.title || "" }));
    },
  });
  const add = useMutation({
    mutationFn: () =>
      api.addYoutube({
        url: url.trim(),
        source_id: meta.source_id,
        title: meta.title.trim() || null,
        caption: meta.caption.trim() || null,
        taxonomy_ids: meta.taxonomy_ids,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "media"] });
      onClose();
    },
  });
  return (
    <Dialog open={open} onClose={onClose} title={t("admin.media.youtube_title")}>
      <div className="flex flex-col gap-5">
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (url.trim() && !lookup.isPending) {
              setFound(null);
              lookup.mutate();
            }
          }}
        >
          <label htmlFor={`${ids}-u`} className={LABEL}>
            {t("admin.media.youtube_url")}
            <input
              id={`${ids}-u`}
              type="url"
              inputMode="url"
              autoCapitalize="off"
              spellCheck={false}
              className={INPUT}
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setFound(null);
              }}
            />
            <span className="text-text-3">{t("admin.media.youtube_url_hint")}</span>
          </label>
          <div>
            <Button type="submit" variant="strong" disabled={!url.trim() || lookup.isPending}>
              {t(lookup.isPending ? "admin.media.youtube_looking" : "admin.media.youtube_lookup")}
            </Button>
          </div>
          <InlineError error={lookup.error} />
        </form>
        {found ? (
          <form
            noValidate
            className="flex flex-col gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              setTried(true);
              if (!meta.source_id || add.isPending || found.embeddable === false) return;
              add.mutate();
            }}
          >
            <dl
              data-testid="youtube-lookup"
              className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-block border border-line-soft bg-surface-2 p-4"
            >
              <dt className="text-ui-sm text-text-3">{t("admin.media.yt_title")}</dt>
              <dd className="m-0 break-words text-text">{found.title ?? t("admin.media.no_title")}</dd>
              <dt className="text-ui-sm text-text-3">{t("admin.media.yt_channel")}</dt>
              <dd className="m-0 break-words text-text">{found.channel_title ?? found.channel_id ?? "—"}</dd>
              <dt className="text-ui-sm text-text-3">{t("admin.media.yt_embeddable")}</dt>
              <dd className="m-0">
                <EmbeddableWord value={found.embeddable} />
              </dd>
              <dt className="text-ui-sm text-text-3">{t("admin.media.yt_duration")}</dt>
              <dd className="m-0 text-text">{fmtSeconds(found.duration_s) || "—"}</dd>
              <dt className="text-ui-sm text-text-3">{t("admin.media.yt_id")}</dt>
              <dd className="m-0 font-mono text-ui-sm text-text-2">{found.youtube_id}</dd>
            </dl>
            {found.existing_media_id ? (
              <p role="status" className="m-0 text-text-2">
                {t("admin.media.yt_existing", { id: found.existing_media_id })}
              </p>
            ) : null}
            {found.embeddable === false ? (
              <p role="alert" className="m-0 text-amber">
                {t("admin.gate.not_embeddable")}
              </p>
            ) : null}
            <MetaForm value={meta} onChange={setMeta} tried={tried} titleRequired={false} use="embed" />
            <InlineError error={add.error} />
            <div className="flex flex-wrap gap-3">
              <Button type="submit" variant="strong" disabled={add.isPending || found.embeddable === false}>
                {t(add.isPending ? "admin.action.adding" : "admin.media.youtube_add")}
              </Button>
              <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
            </div>
          </form>
        ) : null}
      </div>
    </Dialog>
  );
}

function AttachForm({ m }: { m: AdminMedia }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const [exam, setExam] = useState("step1");
  const [choice, setChoice] = useState<SectionChoice>({ chapterId: "", sectionId: "" });
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [bad, setBad] = useState(false);
  const video = m.kind === "video_youtube" || m.kind === "video_own";
  const attach = useMutation({
    mutationFn: (body: { start_s: number | null; end_s: number | null }) =>
      api.attachMedia(choice.sectionId, m.id, video ? body : {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "media"] });
      void qc.invalidateQueries({ queryKey: qk.editor(choice.sectionId) });
      setChoice({ chapterId: choice.chapterId, sectionId: "" });
    },
  });
  const detach = useMutation({
    mutationFn: (sid: string) => api.detachMedia(sid, m.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "media"] }),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    const s = start.trim() ? Number(start) : null;
    const en = end.trim() ? Number(end) : null;
    const ok = (v: number | null) => v === null || (Number.isInteger(v) && v >= 0);
    if (!choice.sectionId || !ok(s) || !ok(en) || (s !== null && en !== null && en <= s)) {
      setBad(true);
      return;
    }
    setBad(false);
    attach.mutate({ start_s: s, end_s: en });
  }
  return (
    <section aria-labelledby={`${ids}-h`} className="flex flex-col gap-4">
      <h3 id={`${ids}-h`} className="m-0 font-semibold text-text">
        {t("admin.media.sections_title")}
      </h3>
      {m.section_ids.length ? (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {m.section_ids.map((sid) => (
            <li key={sid} className="flex items-center gap-3">
              <span className="min-w-0 flex-1 break-all font-mono text-ui-sm text-text-2">{sid}</span>
              <Button
                icon={<Unlink size={16} />}
                disabled={detach.isPending}
                onClick={() => detach.mutate(sid)}
                aria-label={t("admin.media.detach", { id: sid })}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-ui-sm text-text-3">{t("admin.media.sections_none")}</p>
      )}
      <form noValidate onSubmit={submit} className="flex flex-col gap-4">
        <SectionPicker value={choice} onChange={setChoice} exam={exam} onExam={setExam} />
        {video ? (
          <div className="grid gap-4 tablet:grid-cols-2">
            <label htmlFor={`${ids}-s`} className={LABEL}>
              {t("admin.editor.start_s")}
              <input
                id={`${ids}-s`}
                inputMode="numeric"
                className={INPUT}
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label htmlFor={`${ids}-e`} className={LABEL}>
              {t("admin.editor.end_s")}
              <input
                id={`${ids}-e`}
                inputMode="numeric"
                className={INPUT}
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
        ) : null}
        {bad ? (
          <p role="alert" className="m-0 text-ui-sm text-amber">
            {t("admin.media.attach_invalid")}
          </p>
        ) : null}
        <InlineError error={attach.error ?? detach.error} />
        <div>
          <Button type="submit" icon={<Link2 size={16} />} disabled={attach.isPending}>
            {t(attach.isPending ? "admin.action.saving" : "admin.media.attach")}
          </Button>
        </div>
      </form>
    </section>
  );
}

function MediaDetail({ m, onClose }: { m: AdminMedia | null; onClose: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const ids = useId();
  const [meta, setMeta] = useState<MetaFields>(EMPTY_META);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (m) {
      setMeta({
        title: m.title ?? "",
        caption: m.caption ?? "",
        attribution: m.attribution ?? "",
        source_id: m.source_id,
        taxonomy_ids: m.taxonomy_ids,
      });
      setNote("");
    }
  }, [m]);
  const save = useMutation({
    mutationFn: () =>
      api.patchMedia(m?.id ?? "", {
        title: meta.title.trim() || null,
        caption: meta.caption.trim() || null,
        attribution: meta.attribution.trim() || null,
        source_id: meta.source_id,
        taxonomy_ids: meta.taxonomy_ids,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "media"] }),
  });
  const review = useMutation({
    mutationFn: (status: "approved" | "rejected") =>
      api.reviewMedia(m?.id ?? "", { status, note: note.trim() || null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "media"] });
      onClose();
    },
  });
  return (
    <Dialog open={m !== null} onClose={onClose} title={m?.title ?? m?.id ?? ""} wide>
      {m ? (
        <div className="grid gap-6 laptop:grid-cols-2">
          <div className="flex flex-col gap-4">
            <MediaThumb m={m} className="aspect-[4/3] w-full" />
            <div className="flex flex-wrap items-center gap-2">
              <Chip tone="meta">{t(MEDIA_KIND_KEYS[m.kind])}</Chip>
              <StatusChip status={m.review_status} prefix={t("admin.media.review_label")} />
              {m.kind === "video_youtube" ? <EmbeddableWord value={m.embeddable} /> : null}
              {m.duration_s ? <span className="text-ui-sm text-text-2">{fmtSeconds(m.duration_s)}</span> : null}
            </div>
            <p className="m-0 break-all font-mono text-label-sm text-text-3">{m.id}</p>
            {m.kind === "video_youtube" && m.metadata_fetched_at ? (
              <p className="m-0 text-ui-sm text-text-3">
                {t("admin.media.metadata_at", { date: fmtDate(m.metadata_fetched_at) })}
              </p>
            ) : null}
            <AttachForm m={m} />
          </div>
          <div className="flex flex-col gap-6">
            <form
              noValidate
              className="flex flex-col gap-5"
              onSubmit={(e) => {
                e.preventDefault();
                if (meta.source_id && !save.isPending) save.mutate();
              }}
            >
              <MetaForm
                value={meta}
                onChange={setMeta}
                tried
                titleRequired={false}
                use={m.kind === "video_youtube" ? "embed" : "host"}
              />
              <InlineError error={save.error}>
                {isConflict(save.error) ? <p className="m-0 text-text-2">{t("admin.conflict.reload_hint")}</p> : null}
              </InlineError>
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" variant="strong" disabled={save.isPending || !meta.source_id}>
                  {t(save.isPending ? "admin.action.saving" : "admin.action.save")}
                </Button>
                {save.isSuccess ? (
                  <span role="status" className="inline-flex items-center gap-2 text-text-2">
                    <Check aria-hidden="true" size={16} />
                    {t("admin.state.saved")}
                  </span>
                ) : null}
              </div>
            </form>
            <section aria-labelledby={`${ids}-r`} className="flex flex-col gap-3 border-line-soft border-t pt-5">
              <h3 id={`${ids}-r`} className="m-0 font-semibold text-text">
                {t("admin.media.review_title")}
              </h3>
              <label htmlFor={`${ids}-n`} className={LABEL}>
                {t("admin.field.note_optional")}
                <textarea
                  id={`${ids}-n`}
                  className={TEXTAREA}
                  maxLength={1000}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <InlineError error={review.error} />
              <div className="flex flex-wrap gap-3">
                <Button
                  icon={<Check size={16} />}
                  disabled={review.isPending || m.review_status === "approved"}
                  onClick={() => review.mutate("approved")}
                >
                  {t("admin.action.approve")}
                </Button>
                <Button
                  icon={<X size={16} />}
                  disabled={review.isPending || m.review_status === "rejected"}
                  onClick={() => review.mutate("rejected")}
                >
                  {t("admin.action.reject")}
                </Button>
              </div>
            </section>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}

/** `/admin/media`: the media library, uploads (figure, PDF), YouTube by URL, review and section attachments. */
export function MediaPage({ search }: { search: MediaSearch }) {
  const t = useT();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const ids = useId();
  const [file, setFile] = useState<File | null>(null);
  const [typeError, setTypeError] = useState(false);
  const [youtube, setYoutube] = useState(false);
  const [detail, setDetail] = useState<AdminMedia | null>(null);
  const [dragging, setDragging] = useState(false);
  const [q, setQ] = useState(search.q ?? "");
  const kind = search.kind ?? "all";
  const review = search.review ?? "all";
  const params = {
    kind: kind === "all" ? undefined : kind,
    review_status: review === "all" ? undefined : review,
    q: search.q,
    limit: 30,
  };
  const list = useInfiniteQuery({
    queryKey: qk.media(params),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.media({ ...params, cursor: pageParam }, signal),
    getNextPageParam: (p) => p.next_cursor,
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const set = (patch: Partial<MediaSearch>) =>
    void navigate({ to: "/admin/media", search: { ...search, ...patch }, replace: true });

  // Keep the open detail in step with the list after a save.
  useEffect(() => {
    if (!detail) return;
    const fresh = items.find((m) => m.id === detail.id);
    if (fresh && fresh !== detail) setDetail(fresh);
  }, [items, detail]);

  function pick(f: File | undefined) {
    if (!f) return;
    if (!uploadKind(f)) {
      setTypeError(true);
      return;
    }
    setTypeError(false);
    setFile(f);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files[0]);
  }

  return (
    <AdminPage label={t("admin.label")} title={t("admin.media.title")}>
      <div className="flex flex-col gap-4 laptop:flex-row">
        {/* Drop target for a dragged file; the buttons inside are the keyboard and touch path. */}
        {/* biome-ignore lint/a11y/noStaticElementInteractions: drag-and-drop target only; buttons below do the same */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cx(
            "flex min-w-0 flex-[2] flex-col justify-between gap-5 rounded-tile-sm border-2 border-dashed p-6 tablet:p-8",
            dragging ? "border-paper" : "border-line-dashed",
          )}
        >
          <p className="m-0 font-display font-bold text-[clamp(1.5rem,1.1rem+1.6vw,2.25rem)] leading-tight tracking-[-0.03em]">
            {t("admin.media.drop")}
          </p>
          <p id={`${ids}-hint`} className="m-0 text-ui-sm text-text-2">
            {t("admin.media.drop_hint")}
          </p>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="primary"
              size="cta"
              icon={<Upload size={18} />}
              aria-describedby={`${ids}-hint`}
              onClick={() => fileRef.current?.click()}
            >
              {t("admin.media.choose_file")}
            </Button>
            <Button size="cta" icon={<CirclePlay size={18} />} onClick={() => setYoutube(true)}>
              {t("admin.media.youtube_open")}
            </Button>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            tabIndex={-1}
            aria-hidden="true"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              pick(f);
            }}
          />
          {typeError ? (
            <p role="alert" className="m-0 text-amber">
              {t("admin.media.type_error")}
            </p>
          ) : null}
        </div>
      </div>

      <div className="flex flex-col gap-4 laptop:flex-row laptop:items-end laptop:justify-between">
        <div className="flex flex-col gap-4">
          <FilterPills
            label={t("admin.media.kind_label")}
            value={kind}
            options={[
              { value: "all", label: t("admin.filter.all") },
              ...MEDIA_KINDS.filter((k) => k !== "audio" && k !== "link").map((k) => ({
                value: k,
                label: t(MEDIA_KIND_KEYS[k]),
              })),
            ]}
            onChange={(v) => set({ kind: v === "all" ? undefined : v })}
          />
          <FilterPills
            label={t("admin.media.review_label")}
            value={review}
            options={[
              { value: "all", label: t("admin.filter.all") },
              ...REVIEW_STATUSES.map((s) => ({ value: s, label: t(STATUS_KEYS[s] ?? s) })),
            ]}
            onChange={(v) => set({ review: v === "all" ? undefined : v })}
          />
        </div>
        <form
          className="relative flex min-w-0 laptop:w-80"
          onSubmit={(e) => {
            e.preventDefault();
            set({ q: q.trim() || undefined });
          }}
        >
          <label className="flex w-full">
            <span className="sr-only">{t("admin.media.search")}</span>
            <Search
              aria-hidden="true"
              size={16}
              className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
            />
            <input
              type="search"
              value={q}
              placeholder={t("admin.media.search")}
              onChange={(e) => setQ(e.target.value)}
              className={cx(INPUT, "pl-10")}
            />
          </label>
        </form>
      </div>

      {list.isPending ? (
        <Loading rows={3} tall />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : items.length === 0 ? (
        <Empty
          title={t("admin.media.empty")}
          body={t(
            search.q || kind !== "all" || review !== "all" ? "admin.state.empty_filtered" : "admin.media.empty_body",
          )}
        />
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 tablet:grid-cols-2 laptop:grid-cols-3">
          {items.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                data-media-id={m.id}
                onClick={() => setDetail(m)}
                className="flex w-full flex-col gap-3 rounded-block border border-line-soft bg-surface-1 p-4 text-left text-text hover:border-line-control"
              >
                <MediaThumb m={m} className="aspect-[4/3] w-full" />
                <span className="break-words font-semibold">{m.title ?? m.caption ?? t("admin.media.no_title")}</span>
                <span className="flex flex-wrap items-center gap-2">
                  <Chip tone="meta">{t(MEDIA_KIND_KEYS[m.kind])}</Chip>
                  <StatusChip status={m.review_status} prefix={t("admin.media.review_label")} />
                  {m.kind === "video_youtube" ? <EmbeddableWord value={m.embeddable} /> : null}
                </span>
                <span className="text-ui-sm text-text-3">
                  {t("admin.media.used_in", { n: String(m.section_ids.length) })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <MoreButton
        show={Boolean(list.hasNextPage)}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
      <UploadDialog file={file} onClose={() => setFile(null)} />
      <YoutubeDialog open={youtube} onClose={() => setYoutube(false)} />
      <MediaDetail m={detail} onClose={() => setDetail(null)} />
    </AdminPage>
  );
}
