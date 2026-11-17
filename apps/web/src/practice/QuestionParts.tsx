import { cx } from "@naadi/ui";
import { Check, TriangleAlert } from "lucide-react";
import { SafeHtml } from "../content/SafeHtml";
import { useCopy } from "./copy";
import type { WhyNotRow } from "./format";

const ROW =
  "flex w-full min-h-[68px] items-center gap-4 rounded-block border p-3 pr-5 text-[1.0625rem] box-border transition-colors duration-(--duration-control) ease-out";
const DOT = "flex size-11 shrink-0 items-center justify-center rounded-full font-display font-bold text-[1.125rem]";

/** How an answer option looks: the student's pick, the revealed key, a wrong pick, or the key as the admin reviewer sees it. */
export type OptionTone = "idle" | "mine" | "correct" | "wrong" | "key";

export interface OptionRowProps {
  label: string;
  html: string;
  tone: OptionTone;
  /** The small word on the right ("Correct answer", "Your answer", "Key"); it is how status is told without colour. */
  tag?: string;
  /** A student's answer row is a radio input. Without `input`, the row is a static, read-only list item. */
  input?: { name: string; checked: boolean; disabled: boolean; onChange: () => void };
  locked?: boolean;
}

/** One answer option, exactly as the student sees it in Qbank (QuestionPage) and the admin sees it in the review UI. */
export function OptionRow({ label, html, tone, tag = "", input, locked = false }: OptionRowProps) {
  const body = (
    <>
      <span
        aria-hidden="true"
        className={cx(
          DOT,
          tone === "correct"
            ? "bg-lime-ink text-lime"
            : tone === "wrong"
              ? "bg-amber text-amber-ink"
              : tone === "mine"
                ? "bg-ground text-text"
                : tone === "key"
                  ? "bg-text text-ground"
                  : "bg-surface-2 text-text",
        )}
      >
        {label}
      </span>
      <span className="sr-only">{`${label}. `}</span>
      <SafeHtml html={html} className="min-w-0 flex-1 text-left [&_p]:m-0" />
      {tag ? (
        <span className="flex shrink-0 items-center gap-1.5 font-mono text-label-sm tracking-[0.06em] uppercase">
          {tone === "correct" || tone === "key" ? <Check aria-hidden="true" size={14} /> : null}
          {tone === "wrong" ? <TriangleAlert aria-hidden="true" size={14} /> : null}
          {tag}
        </span>
      ) : null}
    </>
  );
  const cls = cx(
    ROW,
    input &&
      "has-[:focus-visible]:outline has-[:focus-visible]:outline-(--focus-ring-width) has-[:focus-visible]:outline-offset-(--focus-ring-offset) has-[:focus-visible]:outline-violet",
    locked || !input ? "cursor-default" : "cursor-pointer",
    tone === "correct"
      ? "border-lime bg-lime text-lime-ink"
      : tone === "wrong"
        ? "border-2 border-amber bg-surface-1 text-text"
        : tone === "mine"
          ? "border-paper bg-paper text-ground"
          : tone === "key"
            ? "border-2 border-text bg-surface-1 text-text"
            : cx("border-line-soft bg-surface-1 text-text", input && !locked && "hover:border-line-control"),
  );
  if (!input) return <li className={cx(cls, "list-none")}>{body}</li>;
  return (
    <label className={cls}>
      <input
        type="radio"
        name={input.name}
        value={label}
        checked={input.checked}
        disabled={input.disabled}
        onChange={input.onChange}
        className="sr-only"
      />
      {body}
    </label>
  );
}

/** The "why the others are wrong" rows (one per option) shown under an explanation. */
export function WhyNotRows({ rows }: { rows: WhyNotRow[] }) {
  const t = useCopy();
  return (
    <div className="flex flex-col">
      <h3 className="m-0 pb-2 font-mono text-label text-text-3 uppercase">{t("practice.why_not")}</h3>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[2.5rem_1fr] gap-4 border-line-soft border-t py-3.5">
          <span className="font-mono text-label text-text-2 uppercase">{r.label}</span>
          <span className="text-text-2">{r.text}</span>
        </div>
      ))}
    </div>
  );
}
