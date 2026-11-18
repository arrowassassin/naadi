import type { QuestionReviewIn, ReviewItemDetail } from "@naadi/shared";
import { Button, cx, Tile, TileLabel } from "@naadi/ui";
import { Check } from "lucide-react";
import { useId, useState } from "react";
import { requestIdOf, reviewErrorKey } from "./api";
import { useReviewCopy } from "./copy";
import { approveBlockedKey, decisionBlockedKey, type Ratings, ratingsComplete } from "./logic";
import { FLAW_CODES } from "./rules";
import { FIELD } from "./ui";

const PILL =
  "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-pill border px-4 text-ui-sm has-[:focus-visible]:outline has-[:focus-visible]:outline-(--focus-ring-width) has-[:focus-visible]:outline-offset-(--focus-ring-offset) has-[:focus-visible]:outline-violet";

function Choice<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
  disabled,
  hint,
}: {
  legend: string;
  name: string;
  value: T | undefined;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  disabled: boolean;
  hint?: string;
}) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
      <legend className="mb-2 p-0 font-mono text-label text-text-3 uppercase">{legend}</legend>
      {hint ? <p className="m-0 text-text-2 text-ui-sm">{hint}</p> : null}
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <label
            key={o.value}
            className={cx(
              PILL,
              value === o.value ? "border-paper bg-paper font-semibold text-ground" : "border-line-control text-text",
              disabled && "cursor-not-allowed opacity-60",
            )}
          >
            <input
              type="radio"
              className="sr-only"
              name={name}
              value={o.value}
              checked={value === o.value}
              disabled={disabled}
              onChange={() => onChange(o.value)}
            />
            {value === o.value ? <Check aria-hidden="true" size={14} /> : null}
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export interface RatingFormProps {
  item: ReviewItemDetail;
  pending: boolean;
  error: unknown;
  onSubmit: (body: QuestionReviewIn) => void;
}

/** Part C: the ratings, a note, "ask for a second review", and the decision. Approve is the view's one lime button. */
export function RatingForm({ item, pending, error, onSubmit }: RatingFormProps) {
  const t = useReviewCopy();
  const ids = useId();
  const [r, setR] = useState<Ratings>({});
  const [flaws, setFlaws] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [second, setSecond] = useState(false);
  const locked = !item.can_review.allowed || pending;
  const set = (patch: Ratings) => setR((prev) => ({ ...prev, ...patch }));
  const yn = [
    { value: "yes", label: t("review.yes") },
    { value: "no", label: t("review.no") },
  ];
  const ynValue = (v: boolean | undefined) => (v === undefined ? undefined : v ? "yes" : "no");

  const notAllowed = item.can_review.allowed
    ? null
    : t(`review.cannot.${item.can_review.reason ?? "status_not_reviewable"}`);
  const decisionBlocked = decisionBlockedKey(item, r);
  const approveBlocked = approveBlockedKey(item, r);
  const reasonKey = approveBlocked ?? null;

  function decide(decision: QuestionReviewIn["decision"]) {
    if (!ratingsComplete(r)) return;
    onSubmit({
      decision,
      ratings: { ...r, flaws: flaws as QuestionReviewIn["ratings"]["flaws"] },
      note: note.trim() ? note.trim() : null,
      request_second: item.required_approvals >= 2 ? false : second,
    });
  }

  return (
    <Tile as="section" aria-labelledby={`${ids}-h`} className="flex flex-col gap-6" data-testid="review-form">
      <TileLabel>
        <span id={`${ids}-h`}>{t("review.form_title")}</span>
      </TileLabel>
      <p className="m-0 text-text-2 text-ui-sm">{t("review.form_intro")}</p>
      <Choice
        legend={t("review.q_accuracy")}
        name={`${ids}-acc`}
        value={r.accuracy}
        disabled={locked}
        onChange={(v) => set({ accuracy: v })}
        options={[
          { value: "correct", label: t("review.accuracy_correct") },
          { value: "minor_issue", label: t("review.accuracy_minor_issue") },
          { value: "incorrect", label: t("review.accuracy_incorrect") },
        ]}
      />
      <Choice
        legend={t("review.q_single")}
        name={`${ids}-sba`}
        value={ynValue(r.single_best_answer)}
        disabled={locked}
        onChange={(v) => set({ single_best_answer: v === "yes" })}
        options={yn}
      />
      <Choice
        legend={t("review.q_cognitive")}
        name={`${ids}-cog`}
        value={r.cognitive_level}
        disabled={locked}
        onChange={(v) => set({ cognitive_level: v })}
        options={[
          { value: "recall", label: t("review.cognitive_recall") },
          { value: "application", label: t("review.cognitive_application") },
          { value: "analysis", label: t("review.cognitive_analysis") },
        ]}
      />
      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-2 p-0 font-mono text-label text-text-3 uppercase">{t("review.q_flaws")}</legend>
        <div className="grid grid-cols-1 gap-x-4 gap-y-1 tablet:grid-cols-2">
          {FLAW_CODES.map((code) => (
            <label
              key={code}
              className="flex min-h-11 cursor-pointer items-center gap-3 text-text-2 has-[:disabled]:cursor-not-allowed"
            >
              <input
                type="checkbox"
                className="size-5 accent-paper"
                checked={flaws.includes(code)}
                disabled={locked}
                onChange={(e) => setFlaws((f) => (e.target.checked ? [...f, code] : f.filter((c) => c !== code)))}
              />
              {t(`review.flaw.${code}`)}
            </label>
          ))}
        </div>
      </fieldset>
      <Choice
        legend={t("review.q_difficulty", {
          labelled: item.difficulty ? t(`review.difficulty_${item.difficulty}`) : t("review.unknown"),
        })}
        name={`${ids}-dif`}
        value={r.difficulty}
        disabled={locked}
        onChange={(v) => set({ difficulty: v })}
        options={[
          { value: "easy", label: t("review.difficulty_easy") },
          { value: "medium", label: t("review.difficulty_medium") },
          { value: "hard", label: t("review.difficulty_hard") },
        ]}
      />
      <Choice
        legend={t("review.q_taxonomy")}
        hint={t("review.taxonomy_tags", { tags: item.taxonomy.map((n) => n.label).join(", ") })}
        name={`${ids}-tax`}
        value={ynValue(r.taxonomy_ok)}
        disabled={locked}
        onChange={(v) => set({ taxonomy_ok: v === "yes" })}
        options={yn}
      />
      <div className="flex flex-col gap-2">
        <label htmlFor={`${ids}-note`} className="font-mono text-label text-text-3 uppercase">
          {t("review.note_label")}
        </label>
        <textarea
          id={`${ids}-note`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          rows={3}
          disabled={locked}
          className={FIELD}
        />
      </div>
      {item.required_approvals >= 2 ? (
        <p className="m-0 text-text-2 text-ui-sm">{t("review.second_required")}</p>
      ) : (
        <label className="flex min-h-11 cursor-pointer items-center gap-3 has-[:disabled]:cursor-not-allowed">
          <input
            type="checkbox"
            className="size-5 accent-paper"
            checked={second}
            disabled={locked}
            onChange={(e) => setSecond(e.target.checked)}
          />
          <span>
            {t("review.second_label")}
            <span className="block text-text-3 text-ui-sm">{t("review.second_hint")}</span>
          </span>
        </label>
      )}
      <div className="flex flex-col gap-3 border-line-soft border-t pt-5">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            size="cta"
            disabled={locked || approveBlocked !== null}
            aria-describedby={`${ids}-why`}
            onClick={() => decide("approve")}
          >
            {t("review.approve")}
          </Button>
          <Button
            variant="secondary"
            disabled={locked || decisionBlocked !== null}
            aria-describedby={`${ids}-why`}
            onClick={() => decide("revise")}
          >
            {t("review.revise")}
          </Button>
          <Button
            variant="secondary"
            disabled={locked || decisionBlocked !== null}
            aria-describedby={`${ids}-why`}
            onClick={() => decide("reject")}
          >
            {t("review.reject")}
          </Button>
        </div>
        <p id={`${ids}-why`} className="m-0 text-text-2 text-ui-sm" data-testid="review-why">
          {pending
            ? t("review.submitting")
            : notAllowed
              ? notAllowed
              : reasonKey
                ? t(reasonKey)
                : t("review.decision_hint")}
        </p>
        {error ? (
          <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text">
            {t(reviewErrorKey(error))}
            {requestIdOf(error) ? (
              <span className="block font-mono text-label-sm text-text-3">{requestIdOf(error)}</span>
            ) : null}
          </p>
        ) : null}
      </div>
    </Tile>
  );
}
