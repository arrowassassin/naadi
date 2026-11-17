/**
 * Official USMLE / NBME practice: LINKS ONLY (segment 23). The materials are "All rights reserved" (FSMB/NBME;
 * docs/research/questions_media_flashcards_licences.md A1-A2): Naadi never stores, re-renders or embeds them, and
 * every link opens on the official site in a new tab. Each entry references a `sources` row (config/sources.yaml).
 * The words come from ui_copy (`official.link.<id>.title` / `.note`); only the URLs live here.
 */
export interface OfficialLink {
  id: string;
  url: string;
  sourceId: "usmle-official-samples" | "usmle-content-outline-2026";
}

export interface OfficialGroup {
  id: "step1" | "step2ck" | "nbme" | "outline";
  links: OfficialLink[];
}

const SAMPLES = "usmle-official-samples" as const;

export const OFFICIAL_GROUPS: readonly OfficialGroup[] = [
  {
    id: "step1",
    links: [
      {
        id: "step1_samples",
        url: "https://www.usmle.org/exam-resources/step-1-materials/step-1-sample-test-questions",
        sourceId: SAMPLES,
      },
      { id: "step1_page", url: "https://www.usmle.org/step-exams/step-1", sourceId: SAMPLES },
    ],
  },
  {
    id: "step2ck",
    links: [
      {
        id: "step2ck_samples",
        url: "https://www.usmle.org/exam-resources/step-2-ck-materials/step-2-ck-sample-test-questions",
        sourceId: SAMPLES,
      },
      { id: "step2ck_page", url: "https://www.usmle.org/step-exams/step-2-ck", sourceId: SAMPLES },
    ],
  },
  {
    id: "nbme",
    links: [
      {
        id: "nbme_cbssa",
        url: "https://www.nbme.org/examinees/self-assessments/comprehensive-basic-science-self-assessment/",
        sourceId: SAMPLES,
      },
      { id: "practice_faq", url: "https://www.usmle.org/common-questions/practice-materials", sourceId: SAMPLES },
    ],
  },
  {
    id: "outline",
    links: [
      {
        id: "content_outline",
        url: "https://www.usmle.org/sites/default/files/2022-01/USMLE_Content_Outline_0.pdf",
        sourceId: "usmle-content-outline-2026",
      },
    ],
  },
];
