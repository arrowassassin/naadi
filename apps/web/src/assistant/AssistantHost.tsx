import { cx } from "@naadi/ui";
import { ShieldAlert, Sparkles } from "lucide-react";
import { lazy, type ReactNode, Suspense, useEffect } from "react";
import { createPortal } from "react-dom";
import { useMe } from "../auth/api";
import { useAuth } from "../auth/session";
import { closeCanvas } from "../canvas/registry";
import { useUiText } from "../copy/useUiText";
import { useActiveExam } from "../exam/api";
import { useAssistantAvailable } from "./ask";
import { closeAssistant, openAssistant } from "./store";
import "./assistant.css";

/*
 * The assistant's mount point in the app shell (segment 14). The surface itself (assistant-ui, Streamdown,
 * TipTap, the canvas) loads lazily once the assistant can answer: the `assistant` feature is on and no
 * timed/exam session is active on the server. While it is mounted it registers the ask handler, so every page's
 * "Ask" affordance exists exactly while the assistant can answer. See AssistantSurface.tsx for the layouts.
 */

const AssistantSurface = lazy(() => import("./AssistantSurface"));

/** Tells the shell the assistant can open (the tab bar's centre button reads it). */
export function AssistantTabButton({ className }: { className?: string }) {
  const t = useUiText();
  const available = useAssistantAvailable();
  if (!available) return null;
  return (
    <li className={cx("flex shrink-0", className)}>
      <button
        type="button"
        onClick={() => openAssistant({ focus: true })}
        aria-label={t("agent.open")}
        data-assistant="tab"
        className="flex size-14 items-center justify-center rounded-pill bg-violet text-violet-ink"
      >
        <Sparkles aria-hidden="true" size={22} />
      </button>
    </li>
  );
}

/** Where the bar would be during a timed/exam session (tablet and up): the assistant is off, and says so. */
function ExamNotice({ dockSlot }: { dockSlot: HTMLElement | null }) {
  const t = useUiText();
  if (!dockSlot) return null;
  return createPortal(
    <p
      role="status"
      data-assistant="exam"
      className="m-0 mx-auto flex min-h-12 w-fit max-w-full items-center gap-2 rounded-pill border border-line-strong bg-bar px-4 text-text-2 text-ui-sm"
    >
      <ShieldAlert aria-hidden="true" size={16} className="shrink-0" />
      {t("agent.exam_mode")}
    </p>,
    dockSlot,
  );
}

/**
 * Mount point in the app shell. `dockSlot` is the shell's bottom-bar element for tablet and up; `onExamRoute`
 * hides the exam notice on the exam screen itself (it shows its own).
 */
export function AssistantHost({
  dockSlot,
  onExamRoute,
}: {
  dockSlot: HTMLElement | null;
  onExamRoute: boolean;
}): ReactNode {
  const auth = useAuth();
  const me = useMe();
  const exam = useActiveExam();
  const enabled = auth.status === "signed-in" && me.data?.features.assistant === true;
  const active = exam.data?.active ?? null;
  // Until the server has answered whether an exam is running, the assistant stays off. If the check itself
  // failed (offline, server down) it mounts and shows that state; the server refuses during an exam anyway.
  const examKnown = exam.data !== undefined || exam.isError;
  useEffect(() => {
    if (!enabled || active) {
      closeAssistant();
      closeCanvas();
    }
  }, [enabled, active]);
  if (!enabled) return null;
  if (active) return onExamRoute ? null : <ExamNotice dockSlot={dockSlot} />;
  if (!examKnown) return null;
  return (
    <Suspense fallback={null}>
      <AssistantSurface dockSlot={dockSlot} />
    </Suspense>
  );
}
