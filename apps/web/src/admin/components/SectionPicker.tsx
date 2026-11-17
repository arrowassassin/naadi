import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { api, EXAMS, qk } from "../api";
import { INPUT, LABEL, useT } from "./ui";

export interface SectionChoice {
  chapterId: string;
  sectionId: string;
}

/** Choose a chapter, then one of its sections (attach media, approve generated media). */
export function SectionPicker({
  value,
  onChange,
  exam,
  onExam,
}: {
  value: SectionChoice;
  onChange: (v: SectionChoice) => void;
  exam: string;
  onExam: (exam: string) => void;
}) {
  const t = useT();
  const ids = useId();
  const chapters = useQuery({
    queryKey: qk.chapters({ exam }),
    queryFn: ({ signal }) => api.chapters({ exam }, signal),
  });
  const chapter = useQuery({
    queryKey: qk.chapter(value.chapterId),
    enabled: Boolean(value.chapterId),
    queryFn: ({ signal }) => api.chapter(value.chapterId, signal),
  });
  return (
    <div className="grid gap-4 tablet:grid-cols-3">
      <label htmlFor={`${ids}-x`} className={LABEL}>
        {t("admin.field.exam")}
        <select
          id={`${ids}-x`}
          className={INPUT}
          value={exam}
          onChange={(e) => {
            onExam(e.target.value);
            onChange({ chapterId: "", sectionId: "" });
          }}
        >
          {EXAMS.map((x) => (
            <option key={x} value={x}>
              {t(x === "step1" ? "admin.exam.step1" : "admin.exam.step2ck")}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={`${ids}-c`} className={LABEL}>
        {t("admin.field.chapter")}
        <select
          id={`${ids}-c`}
          className={INPUT}
          value={value.chapterId}
          disabled={chapters.isPending}
          onChange={(e) => onChange({ chapterId: e.target.value, sectionId: "" })}
        >
          <option value="">{t(chapters.isPending ? "admin.state.loading" : "admin.field.chapter_choose")}</option>
          {(chapters.data?.items ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={`${ids}-s`} className={LABEL}>
        {t("admin.field.section")}
        <select
          id={`${ids}-s`}
          className={INPUT}
          value={value.sectionId}
          disabled={!value.chapterId || chapter.isPending}
          onChange={(e) => onChange({ ...value, sectionId: e.target.value })}
        >
          <option value="">{t("admin.field.section_choose")}</option>
          {[...(chapter.data?.sections ?? [])]
            .sort((a, b) => a.sort - b.sort)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.heading}
              </option>
            ))}
        </select>
      </label>
      {chapters.isError || chapter.isError ? (
        <p role="alert" className="m-0 text-ui-sm text-amber tablet:col-span-3">
          {t("admin.field.section_failed")}
        </p>
      ) : null}
    </div>
  );
}
