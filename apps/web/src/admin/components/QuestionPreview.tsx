import { Button, cx } from "@naadi/ui";
import { Check, Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { SafeHtml } from "../../content/SafeHtml";
import { splitWhyNot } from "../../practice/format";
import { useT } from "./ui";

/* The practice screen's option row and letter dot (practice/QuestionPage.tsx), read-only. */
const ROW = "flex w-full min-h-[68px] items-center gap-4 rounded-block border p-3 pr-5 text-[1.0625rem] box-border";
const DOT = "flex size-11 shrink-0 items-center justify-center rounded-full font-display font-bold text-[1.125rem]";

export interface PreviewQuestion {
  stem_html: string;
  options: { label: string; html: string }[];
  answer: string;
  explanation_html: string | null;
  why_not_html: string | null;
  educational_objective: string | null;
}

/**
 * The question as the student sees it (stem and options with the practice styling). Admin only: "Reveal answer"
 * shows the key, explanation, why-not and objective, as the student sees them after answering.
 */
export function QuestionPreview({ q }: { q: PreviewQuestion }) {
  const t = useT();
  const [revealed, setRevealed] = useState(false);
  const rows = splitWhyNot(q.why_not_html);
  return (
    <section aria-label={t("admin.questions.preview")} className="flex flex-col gap-5" data-question-preview="">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="font-mono text-label text-text-3 uppercase">{t("admin.questions.preview")}</span>
        <Button
          aria-pressed={revealed}
          icon={revealed ? <EyeOff size={16} /> : <Eye size={16} />}
          onClick={() => setRevealed((r) => !r)}
        >
          {t(revealed ? "admin.questions.hide_answer" : "admin.questions.reveal_answer")}
        </Button>
      </div>
      {q.stem_html.trim() ? (
        <SafeHtml html={q.stem_html} className="flex flex-col gap-3 text-reading text-text [&_p]:m-0" />
      ) : (
        <p className="m-0 text-text-3">{t("admin.questions.preview_empty")}</p>
      )}
      <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
        {q.options.map((o) => {
          const key = revealed && o.label === q.answer;
          return (
            <li
              key={o.label}
              className={cx(ROW, key ? "border-paper bg-paper text-ground" : "border-line-soft bg-surface-1 text-text")}
            >
              <span aria-hidden="true" className={cx(DOT, key ? "bg-ground text-text" : "bg-surface-2 text-text")}>
                {o.label}
              </span>
              <span className="sr-only">{`${o.label}. `}</span>
              <SafeHtml html={o.html} className="min-w-0 flex-1 text-left [&_p]:m-0" />
              {key ? (
                <span className="flex shrink-0 items-center gap-1.5 font-mono text-label-sm tracking-[0.06em] uppercase">
                  <Check aria-hidden="true" size={14} />
                  {t("admin.questions.correct_answer")}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
      {revealed ? (
        <div className="flex flex-col gap-4 rounded-tile-sm border border-line-soft bg-surface-1 p-5 tablet:p-6">
          <h3 className="m-0 font-display font-bold text-[1.375rem]">
            {t("admin.questions.why_it_is", { answer: q.answer })}
          </h3>
          {q.explanation_html ? (
            <SafeHtml html={q.explanation_html} className="text-reading text-text-2 [&_p]:m-0 [&_p+p]:mt-3" />
          ) : (
            <p className="m-0 text-text-3">{t("admin.questions.no_explanation")}</p>
          )}
          {rows ? (
            <div className="flex flex-col">
              <h4 className="m-0 pb-2 font-mono text-label text-text-3 uppercase">{t("admin.questions.why_not")}</h4>
              {rows.map((r) => (
                <div key={r.label} className="grid grid-cols-[2.5rem_1fr] gap-4 border-line-soft border-t py-3">
                  <span className="font-mono text-label text-text-2 uppercase">{r.label}</span>
                  <span className="text-text-2">{r.text}</span>
                </div>
              ))}
            </div>
          ) : q.why_not_html ? (
            <SafeHtml html={q.why_not_html} className="text-text-2 [&_p]:m-0" />
          ) : null}
          {q.educational_objective ? (
            <p className="m-0 border-line-soft border-t pt-4 text-text-2">
              <span className="font-mono text-label text-text-3 uppercase">{t("admin.questions.objective")}</span>
              <br />
              {q.educational_objective}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
