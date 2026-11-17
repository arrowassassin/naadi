import { Button, Chip, cx, Tile } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import { type FormEvent, useId, useMemo, useState } from "react";
import { splitWhyNot } from "../../practice/format";
import {
  type AdminQuestion,
  api,
  DIFFICULTIES,
  EXAMS,
  type Exam,
  isConflict,
  OPTION_LETTERS,
  type QuestionFields,
  type QuestionStatus,
  qk,
} from "../api";
import { SourceSelect, TaxonomyPicker } from "../components/pickers";
import { QuestionPreview } from "../components/QuestionPreview";
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
  MONO_AREA,
  StatusChip,
  TEXTAREA,
  useT,
} from "../components/ui";
import { EXAM_KEYS } from "./ChaptersPage";
import { DIFFICULTY_KEYS } from "./QuestionsPage";

export const QUESTION_FLOW: Record<QuestionStatus, QuestionStatus[]> = {
  draft: ["in_review", "auto_checked"],
  auto_checked: ["in_review", "draft"],
  in_review: ["published", "draft"],
  published: ["retired", "in_review"],
  retired: ["draft"],
};
const QMOVE_KEYS: Record<QuestionStatus, string> = {
  draft: "admin.workflow.to_draft",
  auto_checked: "admin.workflow.to_auto_checked",
  in_review: "admin.workflow.to_review",
  published: "admin.workflow.to_published",
  retired: "admin.workflow.to_retired",
};
const SEVERITY: Record<string, { key: string; Icon: typeof Info }> = {
  error: { key: "admin.lint.error", Icon: CircleAlert },
  warning: { key: "admin.lint.warning", Icon: TriangleAlert },
  info: { key: "admin.lint.info", Icon: Info },
};
const ORIGIN_KEYS: Record<string, string> = {
  admin: "admin.revision.origin.admin",
  author: "admin.revision.origin.authoring",
  review: "admin.revision.origin.review",
  seed: "admin.revision.origin.seed",
};

interface Form {
  exam: Exam;
  chapter_id: string;
  stem_html: string;
  options: Record<string, string>;
  answer: string;
  explanation_html: string;
  whyNotMode: "rows" | "raw";
  whyNot: Record<string, string>;
  why_not_raw: string;
  educational_objective: string;
  difficulty: string;
  taxonomy_ids: string[];
  source_id: string;
  // Kept as loaded (not edited here).
  answer_html: string | null;
  block: number | null;
  tags: string[];
}

function formOf(q: AdminQuestion | null): Form {
  const opts: Record<string, string> = Object.fromEntries(OPTION_LETTERS.map((l) => [l, ""]));
  for (const o of q?.options ?? []) opts[o.label] = o.html;
  const rows = splitWhyNot(q?.why_not_html);
  const whyNot: Record<string, string> = Object.fromEntries(OPTION_LETTERS.map((l) => [l, ""]));
  for (const r of rows ?? []) whyNot[r.label] = r.text;
  return {
    exam: (q?.exam as Exam | undefined) ?? "step1",
    chapter_id: q?.chapter_id ?? "",
    stem_html: q?.stem_html ?? "",
    options: opts,
    answer: q?.answer ?? "",
    explanation_html: q?.explanation_html ?? "",
    whyNotMode: q?.why_not_html && !rows ? "raw" : "rows",
    whyNot,
    why_not_raw: q?.why_not_html ?? "",
    educational_objective: q?.educational_objective ?? "",
    difficulty: q?.difficulty ?? "",
    taxonomy_ids: q?.taxonomy_ids ?? [],
    source_id: q?.source_id ?? "",
    answer_html: q?.answer_html ?? null,
    block: q?.block ?? null,
    tags: q?.tags ?? [],
  };
}

/** Why-not rows as the plain "A — … B — …" text the practice screen splits into one row per option. */
export function composeWhyNot(rows: Record<string, string>, answer: string): string | null {
  const parts = OPTION_LETTERS.filter((l) => l !== answer && rows[l]?.trim()).map(
    (l) => `${l} — ${(rows[l] ?? "").trim().replace(/\s+/g, " ")}`,
  );
  return parts.length ? parts.join(" ") : null;
}

function fieldsOf(f: Form): QuestionFields {
  return {
    exam: f.exam,
    chapter_id: f.chapter_id || null,
    block: f.block,
    stem_html: f.stem_html.trim(),
    options: OPTION_LETTERS.map((l) => ({ label: l, html: (f.options[l] ?? "").trim() })),
    answer: f.answer,
    answer_html: f.answer_html,
    explanation_html: f.explanation_html.trim() || null,
    why_not_html: f.whyNotMode === "raw" ? f.why_not_raw.trim() || null : composeWhyNot(f.whyNot, f.answer),
    educational_objective: f.educational_objective.trim() || null,
    difficulty: (f.difficulty || null) as QuestionFields["difficulty"],
    taxonomy_ids: f.taxonomy_ids,
    tags: f.tags,
    source_id: f.source_id,
  };
}

/** Client-side hints (the server validates the same and more). Returns ui_copy keys per field. */
export function questionProblems(f: Form): Record<string, string> {
  const p: Record<string, string> = {};
  if (!f.stem_html.trim()) p.stem = "admin.questions.err_stem";
  for (const l of OPTION_LETTERS) if (!(f.options[l] ?? "").trim()) p[`option_${l}`] = "admin.questions.err_option";
  if (!(OPTION_LETTERS as readonly string[]).includes(f.answer)) p.answer = "admin.questions.err_answer";
  if (!f.source_id) p.source = "admin.field.source_required";
  return p;
}

function StatusDialog({
  q,
  target,
  onClose,
}: {
  q: AdminQuestion;
  target: QuestionStatus | null;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const id = useId();
  const [note, setNote] = useState("");
  const move = useMutation({
    mutationFn: (status: QuestionStatus) =>
      api.questionStatus(q.id, { status, note: note.trim() || null, expected_version: q.version }),
    onSuccess: (next) => {
      qc.setQueryData(qk.question(q.id), next);
      void qc.invalidateQueries({ queryKey: ["admin", "questions"] });
      void qc.invalidateQueries({ queryKey: qk.questionRevisions(q.id) });
      setNote("");
      onClose();
    },
  });
  return (
    <Dialog open={target !== null} onClose={onClose} title={target ? t(QMOVE_KEYS[target]) : ""}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (target && !move.isPending) move.mutate(target);
        }}
      >
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
        <InlineError error={move.error}>
          {isConflict(move.error) ? <p className="m-0 text-text-2">{t("admin.conflict.reload_hint")}</p> : null}
        </InlineError>
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

function LintPanel({ id }: { id: string }) {
  const t = useT();
  const lint = useQuery({ queryKey: qk.lint(id), queryFn: ({ signal }) => api.questionLint(id, signal) });
  return (
    <section aria-labelledby="lint-h" className="flex flex-col gap-3">
      <h2 id="lint-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("admin.lint.title")}
      </h2>
      {lint.isPending ? (
        <Loading rows={1} />
      ) : lint.isError ? (
        <Failure error={lint.error} onRetry={() => void lint.refetch()} retrying={lint.isFetching} />
      ) : !lint.data.available ? (
        <p className="m-0 rounded-block border border-dashed border-line-dashed p-4 text-text-2">
          {t("admin.lint.unavailable")}
        </p>
      ) : lint.data.findings.length === 0 ? (
        <p className="m-0 text-text-2">{t("admin.lint.clean")}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {lint.data.findings.map((f, i) => {
            const s = SEVERITY[f.severity] ?? SEVERITY.info;
            const Icon = s?.Icon ?? Info;
            return (
              <li
                // biome-ignore lint/suspicious/noArrayIndexKey: findings have no id
                key={i}
                className="flex items-start gap-3 rounded-inner border border-line-soft bg-surface-2 p-3"
              >
                <Icon
                  aria-hidden="true"
                  size={16}
                  className={cx("mt-1 shrink-0", f.severity !== "info" && "text-amber")}
                />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="text-ui-sm font-semibold text-text">
                    {t(s?.key ?? "admin.lint.info")} · <span className="font-mono">{f.rule}</span>
                  </span>
                  {f.field ? (
                    <span className="text-ui-sm text-text-2">{t("admin.lint.field", { field: f.field })}</span>
                  ) : null}
                  {f.detail && Object.keys(f.detail).length ? (
                    <span className="break-all font-mono text-label-sm text-text-3">{JSON.stringify(f.detail)}</span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Revisions({ id }: { id: string }) {
  const t = useT();
  const list = useQuery({
    queryKey: qk.questionRevisions(id),
    queryFn: ({ signal }) => api.questionRevisions(id, signal),
  });
  return (
    <section aria-labelledby="qrev-h" className="flex flex-col gap-3">
      <h2 id="qrev-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("admin.revision.title")}
      </h2>
      {list.isPending ? (
        <Loading rows={1} />
      ) : list.isError ? (
        <Failure error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <Empty title={t("admin.revision.empty")} />
      ) : (
        <ol className="m-0 flex list-none flex-col p-0">
          {list.data.items.map((r) => (
            <li
              key={r.id}
              className="flex flex-wrap items-center gap-2 border-line-soft border-t py-2 first:border-t-0"
            >
              <span className="font-mono text-label-sm text-text-3">
                {t("admin.revision.version", { n: String(r.version) })}
              </span>
              {r.status ? <StatusChip status={r.status} /> : null}
              <span className="text-ui-sm text-text-2">{fmtDateTime(r.created_at)}</span>
              <span className="text-ui-sm text-text-3">
                {t(ORIGIN_KEYS[r.origin] ?? "admin.revision.origin.other")}
              </span>
              {r.note ? <span className="w-full break-words text-ui-sm text-text-2">{r.note}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Editor({
  q,
  notice,
  setNotice,
}: {
  q: AdminQuestion | null;
  notice: string | null;
  setNotice: (n: string | null) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const ids = useId();
  const [f, setF] = useState<Form>(() => formOf(q));
  const [base, setBase] = useState(() => JSON.stringify(fieldsOf(formOf(q))));
  const [tried, setTried] = useState(false);
  const [target, setTarget] = useState<QuestionStatus | null>(null);
  const chapters = useQuery({
    queryKey: qk.chapters({ exam: f.exam }),
    queryFn: ({ signal }) => api.chapters({ exam: f.exam }, signal),
  });
  const fields = useMemo(() => fieldsOf(f), [f]);
  const dirty = JSON.stringify(fields) !== base;
  const problems = questionProblems(f);
  const bad = Object.keys(problems).length > 0;
  const save = useMutation({
    mutationFn: () =>
      q ? api.putQuestion(q.id, { ...fields, expected_version: q.version }) : api.createQuestion(fields),
    onSuccess: (next) => {
      setNotice(q?.status === "published" && next.status === "in_review" ? "admin.questions.moved_to_review" : null);
      qc.setQueryData(qk.question(next.id), next);
      void qc.invalidateQueries({ queryKey: ["admin", "questions"] });
      void qc.invalidateQueries({ queryKey: qk.lint(next.id) });
      void qc.invalidateQueries({ queryKey: qk.questionRevisions(next.id) });
      setBase(JSON.stringify(fieldsOf(formOf(next))));
      if (!q) void navigate({ to: "/admin/questions/$questionId", params: { questionId: next.id }, replace: true });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (bad || !dirty || save.isPending) return;
    save.mutate();
  }
  const set = (patch: Partial<Form>) => {
    setF((x) => ({ ...x, ...patch }));
    setNotice(null);
  };
  const err = (k: string) => (tried && problems[k] ? problems[k] : null);
  const moves = q ? (QUESTION_FLOW[q.status] ?? []) : [];

  return (
    <AdminPage
      label={t("admin.questions.title")}
      title={q ? t("admin.questions.edit_title", { id: q.id }) : t("admin.questions.new_title")}
      actions={
        q && !dirty
          ? moves.map((m, i) => (
              <Button key={m} variant={i === 0 ? "primary" : "secondary"} size="cta" onClick={() => setTarget(m)}>
                {t(QMOVE_KEYS[m])}
              </Button>
            ))
          : null
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/admin/questions" className="min-h-11 text-text-2">
          {t("admin.questions.back")}
        </Link>
        {q ? (
          <StatusChip status={q.status} prefix={t("admin.field.status")} />
        ) : (
          <Chip tone="planned">{t("admin.status.new")}</Chip>
        )}
        {q ? <Chip tone="meta">{t("admin.revision.version", { n: q.version })}</Chip> : null}
      </div>
      {notice ? (
        <p
          role="status"
          className="m-0 flex items-center gap-2 rounded-block border border-amber/60 bg-surface-2 p-4 text-text"
        >
          <Info aria-hidden="true" size={18} className="shrink-0 text-amber" />
          {t(notice)}
        </p>
      ) : null}
      <div className="grid items-start gap-5 laptop:grid-cols-[3fr_2fr]">
        <Tile as="section" aria-label={t("admin.questions.form")} className="min-w-0">
          <form noValidate onSubmit={submit} className="flex flex-col gap-6">
            <div className="grid gap-4 tablet:grid-cols-2">
              <label htmlFor={`${ids}-x`} className={LABEL}>
                {t("admin.field.exam")}
                <select
                  id={`${ids}-x`}
                  className={INPUT}
                  value={f.exam}
                  onChange={(e) => set({ exam: e.target.value as Exam, chapter_id: "" })}
                >
                  {EXAMS.map((x) => (
                    <option key={x} value={x}>
                      {t(EXAM_KEYS[x] ?? x)}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={`${ids}-c`} className={LABEL}>
                {t("admin.field.chapter_optional")}
                <select
                  id={`${ids}-c`}
                  className={INPUT}
                  value={f.chapter_id}
                  onChange={(e) => set({ chapter_id: e.target.value })}
                >
                  <option value="">{t("admin.field.chapter_none")}</option>
                  {(chapters.data?.items ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                  {f.chapter_id && !chapters.data?.items.some((c) => c.id === f.chapter_id) ? (
                    <option value={f.chapter_id}>{f.chapter_id}</option>
                  ) : null}
                </select>
              </label>
            </div>
            <label htmlFor={`${ids}-stem`} className={LABEL}>
              {t("admin.questions.stem")}
              <textarea
                id={`${ids}-stem`}
                className={cx(MONO_AREA, "min-h-40")}
                value={f.stem_html}
                maxLength={20_000}
                aria-invalid={Boolean(err("stem")) || undefined}
                onChange={(e) => set({ stem_html: e.target.value })}
              />
              <span className={err("stem") ? "text-amber" : "text-text-3"}>
                {t(err("stem") ?? "admin.questions.html_hint")}
              </span>
            </label>
            <fieldset className="m-0 flex flex-col gap-4 border-0 p-0">
              <legend className="mb-2 p-0 text-ui-sm text-text-2">{t("admin.questions.options")}</legend>
              {OPTION_LETTERS.map((l) => (
                <div key={l} className="flex flex-col gap-2 rounded-block border border-line-soft p-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex min-h-11 cursor-pointer items-center gap-2 text-ui-sm text-text">
                      <input
                        type="radio"
                        name={`${ids}-key`}
                        value={l}
                        checked={f.answer === l}
                        onChange={() => set({ answer: l })}
                        className="size-5 accent-paper"
                      />
                      {t("admin.questions.key_is", { letter: l })}
                    </label>
                    {f.answer === l ? <Chip tone="current">{t("admin.questions.key")}</Chip> : null}
                  </div>
                  <label htmlFor={`${ids}-o${l}`} className={LABEL}>
                    {t("admin.questions.option", { letter: l })}
                    <textarea
                      id={`${ids}-o${l}`}
                      className={cx(MONO_AREA, "min-h-20")}
                      value={f.options[l] ?? ""}
                      maxLength={5000}
                      aria-invalid={Boolean(err(`option_${l}`)) || undefined}
                      onChange={(e) => set({ options: { ...f.options, [l]: e.target.value } })}
                    />
                    {err(`option_${l}`) ? <span className="text-amber">{t(err(`option_${l}`) ?? "")}</span> : null}
                  </label>
                  {f.whyNotMode === "rows" && f.answer !== l ? (
                    <label htmlFor={`${ids}-w${l}`} className={LABEL}>
                      {t("admin.questions.why_not_option", { letter: l })}
                      <textarea
                        id={`${ids}-w${l}`}
                        className={cx(TEXTAREA, "min-h-16")}
                        value={f.whyNot[l] ?? ""}
                        maxLength={2000}
                        onChange={(e) => set({ whyNot: { ...f.whyNot, [l]: e.target.value } })}
                      />
                    </label>
                  ) : null}
                </div>
              ))}
              {tried && problems.answer ? <span className="text-ui-sm text-amber">{t(problems.answer)}</span> : null}
            </fieldset>
            {f.whyNotMode === "raw" ? (
              <label htmlFor={`${ids}-wr`} className={LABEL}>
                {t("admin.questions.why_not_raw")}
                <textarea
                  id={`${ids}-wr`}
                  className={MONO_AREA}
                  value={f.why_not_raw}
                  maxLength={20_000}
                  onChange={(e) => set({ why_not_raw: e.target.value })}
                />
                <span className="text-text-3">{t("admin.questions.why_not_raw_hint")}</span>
              </label>
            ) : null}
            <label htmlFor={`${ids}-ex`} className={LABEL}>
              {t("admin.questions.explanation")}
              <textarea
                id={`${ids}-ex`}
                className={cx(MONO_AREA, "min-h-40")}
                value={f.explanation_html}
                maxLength={40_000}
                onChange={(e) => set({ explanation_html: e.target.value })}
              />
            </label>
            <label htmlFor={`${ids}-obj`} className={LABEL}>
              {t("admin.questions.objective")}
              <textarea
                id={`${ids}-obj`}
                className={TEXTAREA}
                value={f.educational_objective}
                maxLength={2000}
                onChange={(e) => set({ educational_objective: e.target.value })}
              />
            </label>
            <label htmlFor={`${ids}-d`} className={LABEL}>
              {t("admin.questions.difficulty_label")}
              <select
                id={`${ids}-d`}
                className={INPUT}
                value={f.difficulty}
                onChange={(e) => set({ difficulty: e.target.value })}
              >
                <option value="">{t("admin.questions.difficulty.none")}</option>
                {DIFFICULTIES.map((d) => (
                  <option key={d} value={d}>
                    {t(DIFFICULTY_KEYS[d] ?? d)}
                  </option>
                ))}
              </select>
            </label>
            <TaxonomyPicker exam={f.exam} value={f.taxonomy_ids} onChange={(taxonomy_ids) => set({ taxonomy_ids })} />
            <SourceSelect
              value={f.source_id}
              onChange={(source_id) => set({ source_id })}
              invalid={Boolean(err("source"))}
            />
            {err("source") ? <span className="text-ui-sm text-amber">{t(err("source") ?? "")}</span> : null}
            <InlineError error={save.error}>
              {isConflict(save.error) ? <p className="m-0 text-text-2">{t("admin.conflict.reload_hint")}</p> : null}
            </InlineError>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="submit"
                variant={dirty || !q ? "primary" : "secondary"}
                size="cta"
                disabled={!dirty || save.isPending}
              >
                {t(save.isPending ? "admin.action.saving" : q ? "admin.action.save" : "admin.questions.create")}
              </Button>
              {q && !dirty && save.isSuccess ? (
                <span role="status" className="text-text-2">
                  {t("admin.state.saved")}
                </span>
              ) : null}
              {tried && bad ? <span className="text-ui-sm text-amber">{t("admin.questions.fix_fields")}</span> : null}
            </div>
          </form>
        </Tile>
        <div className="flex min-w-0 flex-col gap-6">
          <Tile as="div" className="min-w-0">
            <QuestionPreview
              q={{
                stem_html: f.stem_html,
                options: fields.options,
                answer: f.answer,
                explanation_html: fields.explanation_html ?? null,
                why_not_html: fields.why_not_html ?? null,
                educational_objective: fields.educational_objective ?? null,
              }}
            />
          </Tile>
          {q ? (
            <Tile as="div" className="flex min-w-0 flex-col gap-6">
              <LintPanel id={q.id} />
              <Revisions id={q.id} />
            </Tile>
          ) : null}
        </div>
      </div>
      {q ? <StatusDialog q={q} target={target} onClose={() => setTarget(null)} /> : null}
    </AdminPage>
  );
}

/** `/admin/questions/$questionId` (`new` creates one): the full item form, preview, lint and history. */
export function QuestionPage({ questionId }: { questionId: string }) {
  const t = useT();
  const isNew = questionId === "new";
  // Survives the form's re-key after a save (e.g. "moved back to review").
  const [notice, setNotice] = useState<string | null>(null);
  const q = useQuery({
    queryKey: qk.question(questionId),
    enabled: !isNew,
    queryFn: ({ signal }) => api.question(questionId, signal),
    refetchOnWindowFocus: false,
  });
  if (isNew) return <Editor q={null} notice={notice} setNotice={setNotice} />;
  if (q.isPending) return <Loading rows={4} tall />;
  if (q.isError) {
    return (
      <AdminPage label={t("admin.questions.title")} title={t("admin.questions.edit_title", { id: questionId })}>
        <Failure error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </AdminPage>
    );
  }
  // Re-key on the version so a save or status change starts the form from the server's row.
  return <Editor key={q.data.version} q={q.data} notice={notice} setNotice={setNotice} />;
}
