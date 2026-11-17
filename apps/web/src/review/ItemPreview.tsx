import type { ReviewItemDetail } from "@naadi/shared";
import { Chip } from "@naadi/ui";
import { SafeHtml } from "../content/SafeHtml";
import { splitWhyNot } from "../practice/format";
import { OptionRow, WhyNotRows } from "../practice/QuestionParts";
import { useReviewCopy } from "./copy";

/** The item exactly as a student sees it (the Qbank option rows and explanation layout), with the key and the
 * explanations revealed and labelled: this is an admin-only surface. */
export function ItemPreview({ item }: { item: ReviewItemDetail }) {
  const t = useReviewCopy();
  const rows = splitWhyNot(item.why_not_html);
  return (
    <section aria-labelledby="rv-preview-h" className="flex flex-col gap-6" data-testid="review-preview">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="rv-preview-h" className="m-0 font-mono text-label text-text-3 uppercase">
          {t("review.preview_title")}
        </h2>
        <Chip tone="meta">{t("review.preview_admin_only")}</Chip>
      </div>
      <p className="m-0 text-text-2 text-ui-sm">{t("review.preview_note")}</p>
      {item.taxonomy.length ? (
        <p className="m-0 font-mono text-label text-text-3 uppercase">
          {item.taxonomy.map((n) => n.label).join(" · ")}
        </p>
      ) : null}
      <SafeHtml html={item.stem_html} className="flex flex-col gap-3 text-reading text-text [&_p]:m-0" />
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0" aria-label={t("review.options_label")}>
        {item.options.map((o) => (
          <OptionRow
            key={o.label}
            label={o.label}
            html={o.html}
            tone={o.label === item.answer ? "key" : "idle"}
            tag={o.label === item.answer ? t("review.key") : ""}
            locked
          />
        ))}
      </ul>
      <section
        aria-label={t("review.explanation_label")}
        className="flex flex-col gap-4 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-8"
      >
        <h3 className="m-0 font-bold font-display text-[clamp(1.375rem,1.1rem+1vw,1.75rem)] tracking-[-0.02em]">
          {t("review.why_it_is", { answer: item.answer })}
        </h3>
        {item.explanation_html ? (
          <SafeHtml html={item.explanation_html} className="text-reading text-text-2 [&_p]:m-0 [&_p+p]:mt-3" />
        ) : (
          <p className="m-0 text-text-3">{t("review.no_explanation")}</p>
        )}
        {rows ? (
          <WhyNotRows rows={rows} />
        ) : item.why_not_html ? (
          <SafeHtml html={item.why_not_html} className="text-text-2 [&_p]:m-0" />
        ) : null}
        {item.educational_objective ? (
          <p className="m-0 border-line-soft border-t pt-4 text-text-2">
            <span className="font-mono text-label text-text-3 uppercase">{t("review.objective")}</span>
            <br />
            {item.educational_objective}
          </p>
        ) : null}
      </section>
    </section>
  );
}
