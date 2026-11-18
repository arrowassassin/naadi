import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import type { QueueRef } from "../api";
import { refLink } from "../queues/refs";
import { useT } from "./ui";

const REF_KEYS: Record<string, string> = {
  section: "admin.ref.section",
  question: "admin.ref.question",
  chapter: "admin.ref.chapter",
  media: "admin.ref.media",
  youtube_candidate: "admin.ref.media",
  report: "admin.ref.report",
  generated_media: "admin.ref.generated_media",
  pathway_task: "admin.ref.pathway_task",
};

/** Link with a target computed at run time (refs.ts builds only admin routes and their params). */
export const DynamicLink = Link as unknown as ComponentType<
  Record<string, unknown> & { className?: string; children?: ReactNode }
>;

/** "Open the section/question/…" for a ref; plain text when the ref has no admin page. */
export function RefLink({ refTo }: { refTo: QueueRef | null | undefined }) {
  const t = useT();
  if (!refTo) return null;
  const word = t(REF_KEYS[refTo.type] ?? "admin.ref.other");
  const link = refLink(refTo);
  if (!link) {
    return (
      <span className="text-ui-sm text-text-2">
        {word} <span className="font-mono text-label-sm text-text-3">{refTo.id}</span>
      </span>
    );
  }
  return (
    // The target is one of the admin routes in refs.ts; its params are built there.
    <DynamicLink {...link} className="inline-flex min-h-11 items-center gap-1.5 text-ui-sm text-text">
      {t("admin.ref.open", { what: word })}
      <span className="font-mono text-label-sm text-text-3">{refTo.id}</span>
      <ArrowUpRight aria-hidden="true" size={14} />
    </DynamicLink>
  );
}
