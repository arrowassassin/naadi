import { cx } from "@naadi/ui";
import { ShieldAlert, Sparkles } from "lucide-react";
import { type ComponentType, type ReactNode, useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMe } from "../auth/api";
import { useAuth } from "../auth/session";
import { closeCanvas } from "../canvas/registry";
import { useUiText } from "../copy/useUiText";
import { useActiveExam } from "../exam/api";
import { ChunkUnavailable } from "../shell/ChunkUnavailable";
import { isChunkLoadError } from "../shell/lazyPage";
import { useAssistantAvailable } from "./ask";
import { closeAssistant, openAssistant } from "./store";
import "./assistant.css";

/*
 * The assistant's mount point in the app shell (segment 14). The surface itself (assistant-ui, Streamdown,
 * TipTap, the canvas) loads lazily once the assistant can answer: the `assistant` feature is on and no
 * timed/exam session is active on the server. While it is mounted it registers the ask handler, so every page's
 * "Ask" affordance exists exactly while the assistant can answer. See AssistantSurface.tsx for the layouts.
 */

type Surface = ComponentType<{ dockSlot: HTMLElement | null }>;

type SurfaceModule = Promise<{ default: Surface }>;
type ImportSurface = () => SurfaceModule;
const importSurface: ImportSurface = () => import("./AssistantSurface");
let importer = importSurface;
let surfaceLoad: Promise<Surface> | null = null;

/** Tests only: replace the chunk import (null restores the real one). */
export function setSurfaceLoaderForTests(load: ImportSurface | null): void {
  importer = load ?? importSurface;
  surfaceLoad = null;
}

function loadSurface(): Promise<Surface> {
  surfaceLoad ??= importer().then(
    (m) => m.default,
    (err: unknown) => {
      surfaceLoad = null; // the next try fetches again
      throw err;
    },
  );
  return surfaceLoad;
}

/*
 * Loads the surface chunk with seg22's chunk-failure rules (lead decision 20261117T124555; same detection and
 * notice as lazyWithFallback). It is not lazyWithFallback itself because the surface renders nothing in place
 * (only portals and overlays), so a notice in place would land below the shell: here the "needs a connection"
 * notice goes where the assistant bar would be (tablet and up). On a phone nothing shows, the same as while the
 * chunk loads: the tab button and every Ask affordance appear only once the surface has registered its handler,
 * so no button does nothing. It retries on `online` (ChunkUnavailable) and on Try again; any other error is
 * rethrown to the shell's error boundary.
 */
function LazySurface({ dockSlot }: { dockSlot: HTMLElement | null }) {
  const [state, setState] = useState<{ C: Surface } | "loading" | "unavailable">("loading");
  const [attempt, setAttempt] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt is the retry trigger
  useEffect(() => {
    let live = true;
    loadSurface().then(
      (C) => live && setState({ C }),
      (err: unknown) => {
        if (!live) return;
        if (isChunkLoadError(err)) setState("unavailable");
        else
          setState(() => {
            throw err;
          });
      },
    );
    return () => {
      live = false;
    };
  }, [attempt]);
  const retry = useCallback(() => {
    setState("loading");
    setAttempt((n) => n + 1);
  }, []);
  if (state === "loading") return null;
  if (state === "unavailable") {
    if (!dockSlot) return <OfflineRetry onRetry={retry} />;
    return createPortal(
      <div data-assistant="unavailable" className="mx-auto w-fit max-w-full px-4">
        <ChunkUnavailable variant="inline" onRetry={retry} />
      </div>,
      dockSlot,
    );
  }
  return <state.C dockSlot={dockSlot} />;
}

/** Phone: nothing visible, but still retry when the device comes back online. */
function OfflineRetry({ onRetry }: { onRetry: () => void }) {
  useEffect(() => {
    window.addEventListener("online", onRetry);
    return () => window.removeEventListener("online", onRetry);
  }, [onRetry]);
  return null;
}

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
  return <LazySurface dockSlot={dockSlot} />;
}
