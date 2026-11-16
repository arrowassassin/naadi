import { ListChecks } from "lucide-react";
import { MediaUnavailable } from "./MediaNodes";
import { SafeHtml } from "./SafeHtml";
import type { ContentEnv } from "./types";

/**
 * A practice question inside a reading section: stem and options only. The API never sends the answer,
 * explanation or statistics for it; answering arrives with the Qbank (segment 08).
 */
export function QuestionEmbed({ questionId, env }: { questionId: string | null; env: ContentEnv }) {
  const q = questionId ? env.questions[questionId] : undefined;
  if (!q)
    return (
      <MediaUnavailable
        message={env.text("content.question_missing", "This practice question is not available right now.")}
        mediaId={questionId}
        env={env}
      />
    );
  return (
    <section
      data-node="question-embed"
      data-question-id={q.id}
      aria-label="Practice question"
      className="flex flex-col gap-4 rounded-block border border-line bg-surface-1 p-4 tablet:p-5"
    >
      <span className="flex items-center gap-2 font-mono text-label text-text-3 uppercase">
        <ListChecks aria-hidden="true" size={16} />
        Practice question
      </span>
      <SafeHtml html={q.stem_html} className="flex flex-col gap-3 text-text [&_p]:m-0" />
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {q.options.map((o) => (
          <li
            key={o.label}
            className="flex gap-3 rounded-inner border border-line-soft bg-surface-2 px-3 py-2.5 text-text-2"
          >
            <span className="font-mono font-medium text-text">{o.label}</span>
            <SafeHtml html={o.html} className="min-w-0 [&_p]:m-0" />
          </li>
        ))}
      </ol>
    </section>
  );
}
