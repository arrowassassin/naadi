import { Button } from "@naadi/ui";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { AdminMedia, AdminQuestionSummary, MediaKind } from "../api";
import { Dialog, INPUT, LABEL, MONO_AREA, useT } from "../components/ui";
import { MediaPicker, QuestionPicker } from "./pickers";

/** The atom node being inserted (`pos` null) or edited (`pos` = its position). */
export interface NodeDialogState {
  type: string;
  attrs: Record<string, unknown>;
  pos: number | null;
}

const MEDIA_KINDS: Record<string, readonly MediaKind[]> = {
  figure: ["svg", "image"],
  video: ["video_youtube", "video_own"],
  pdf: ["pdf"],
};

const TITLE_KEYS: Record<string, string> = {
  figure: "admin.editor.dialog.figure",
  video: "admin.editor.dialog.video",
  pdf: "admin.editor.dialog.pdf",
  questionEmbed: "admin.editor.dialog.question",
  mermaid: "admin.editor.dialog.mermaid",
  math: "admin.editor.dialog.math",
};

const INT = /^\d{1,6}$/;

function intOrNull(s: string): number | null {
  return INT.test(s.trim()) ? Number.parseInt(s.trim(), 10) : null;
}

/** Validation of the dialog fields (the same bounds as the shared zod schema); null = ok, else a ui_copy key. */
export function validateAtom(type: string, a: Record<string, unknown>): string | null {
  const id = /^[a-z0-9][a-z0-9_-]{0,127}$/;
  switch (type) {
    case "figure":
    case "video":
    case "pdf":
      if (typeof a.mediaId !== "string" || !id.test(a.mediaId)) return "admin.editor.err_media";
      if (type === "video") {
        const s = a.startS as number | null;
        const e = a.endS as number | null;
        if (s !== null && e !== null && e <= s) return "admin.editor.err_range";
      }
      if (type === "pdf" && a.page !== null && (typeof a.page !== "number" || a.page < 1)) {
        return "admin.editor.err_page";
      }
      return null;
    case "questionEmbed":
      return typeof a.questionId === "string" && id.test(a.questionId) ? null : "admin.editor.err_question";
    case "mermaid":
      return typeof a.code === "string" && a.code.trim() && a.code.length <= 20_000 ? null : "admin.editor.err_code";
    case "math":
      return typeof a.latex === "string" && a.latex.trim() && a.latex.length <= 5_000 ? null : "admin.editor.err_latex";
    default:
      return "admin.editor.err_unknown";
  }
}

export function NodeDialog({
  state,
  exam,
  onClose,
  onApply,
  onMedia,
  onQuestion,
}: {
  state: NodeDialogState | null;
  exam: string | undefined;
  onClose: () => void;
  onApply: (s: NodeDialogState) => void;
  onMedia: (m: AdminMedia) => void;
  onQuestion: (q: AdminQuestionSummary) => void;
}) {
  const t = useT();
  const ids = useId();
  const [attrs, setAttrs] = useState<Record<string, unknown>>({});
  const [startS, setStart] = useState("");
  const [endS, setEnd] = useState("");
  const [page, setPage] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!state) return;
    setAttrs(state.attrs);
    setStart(typeof state.attrs.startS === "number" ? String(state.attrs.startS) : "");
    setEnd(typeof state.attrs.endS === "number" ? String(state.attrs.endS) : "");
    setPage(typeof state.attrs.page === "number" ? String(state.attrs.page) : "");
    setError(null);
  }, [state]);
  const type = state?.type ?? "";

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!state) return;
    const out: Record<string, unknown> = { ...attrs };
    if (type === "video") {
      if ((startS.trim() && intOrNull(startS) === null) || (endS.trim() && intOrNull(endS) === null)) {
        setError("admin.editor.err_seconds");
        return;
      }
      out.startS = intOrNull(startS);
      out.endS = intOrNull(endS);
    }
    if (type === "pdf") {
      if (page.trim() && intOrNull(page) === null) {
        setError("admin.editor.err_page");
        return;
      }
      out.page = intOrNull(page);
    }
    if (type === "mermaid") out.code = String(out.code ?? "");
    if (type === "math") out.latex = String(out.latex ?? "");
    const bad = validateAtom(type, out);
    if (bad) {
      setError(bad);
      return;
    }
    onApply({ type, attrs: out, pos: state.pos });
  }

  const kinds = MEDIA_KINDS[type];
  return (
    <Dialog open={state !== null} onClose={onClose} title={t(TITLE_KEYS[type] ?? "admin.editor.dialog.node")} wide>
      <form noValidate onSubmit={submit} className="flex flex-col gap-5">
        {kinds ? (
          <MediaPicker
            kinds={kinds}
            value={typeof attrs.mediaId === "string" ? attrs.mediaId : null}
            onPick={(m) => {
              onMedia(m);
              setAttrs((a) => ({ ...a, mediaId: m.id }));
              setError(null);
            }}
          />
        ) : null}
        {type === "video" ? (
          <div className="grid gap-4 tablet:grid-cols-2">
            <label htmlFor={`${ids}-s`} className={LABEL}>
              {t("admin.editor.start_s")}
              <input
                id={`${ids}-s`}
                inputMode="numeric"
                className={INPUT}
                value={startS}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label htmlFor={`${ids}-e`} className={LABEL}>
              {t("admin.editor.end_s")}
              <input
                id={`${ids}-e`}
                inputMode="numeric"
                className={INPUT}
                value={endS}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
        ) : null}
        {type === "pdf" ? (
          <label htmlFor={`${ids}-p`} className={LABEL}>
            {t("admin.editor.pdf_page")}
            <input
              id={`${ids}-p`}
              inputMode="numeric"
              className={INPUT}
              value={page}
              onChange={(e) => setPage(e.target.value)}
            />
          </label>
        ) : null}
        {type === "questionEmbed" ? (
          <QuestionPicker
            exam={exam}
            value={typeof attrs.questionId === "string" ? attrs.questionId : null}
            onPick={(q) => {
              onQuestion(q);
              setAttrs((a) => ({ ...a, questionId: q.id }));
              setError(null);
            }}
          />
        ) : null}
        {type === "mermaid" || type === "math" ? (
          <label htmlFor={`${ids}-c`} className={LABEL}>
            {t(type === "mermaid" ? "admin.editor.mermaid_code" : "admin.editor.math_latex")}
            <textarea
              id={`${ids}-c`}
              className={`${MONO_AREA} min-h-48`}
              spellCheck={false}
              autoCapitalize="off"
              maxLength={type === "mermaid" ? 20_000 : 5_000}
              value={String((type === "mermaid" ? attrs.code : attrs.latex) ?? "")}
              onChange={(e) => {
                const v = e.target.value;
                setAttrs((a) => ({ ...a, [type === "mermaid" ? "code" : "latex"]: v }));
              }}
            />
            <span className="text-text-3">
              {t(type === "mermaid" ? "admin.editor.mermaid_hint" : "admin.editor.math_hint")}
            </span>
          </label>
        ) : null}
        {error ? (
          <p role="alert" className="m-0 text-amber">
            {t(error)}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong">
            {t(state?.pos === null ? "admin.editor.insert" : "admin.editor.apply")}
          </Button>
          <Button onClick={onClose}>{t("admin.action.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
