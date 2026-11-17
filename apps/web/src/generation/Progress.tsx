import { useGenerationCopy } from "./copy";

export type Stage = "queued" | "generating" | "saving";

const STEP: Record<Stage, { now: number; width: string }> = {
  queued: { now: 1, width: "w-1/3" },
  generating: { now: 2, width: "w-2/3" },
  saving: { now: 3, width: "w-full" },
};

/** Words and a bar: the stage is never only a colour or a width. */
export function GenerationProgress({ stage, queuePosition }: { stage: Stage; queuePosition?: number | null }) {
  const t = useGenerationCopy();
  const word =
    stage === "queued"
      ? queuePosition && queuePosition > 0
        ? t("generation.stage_queued_ahead", { n: queuePosition })
        : t("generation.stage_queued")
      : stage === "generating"
        ? t("generation.stage_generating")
        : t("generation.stage_saving");
  const step = STEP[stage];
  return (
    <div className="flex flex-col gap-2" data-testid="generation-progress" data-stage={stage}>
      <div
        role="progressbar"
        aria-label={t("generation.progress_label")}
        aria-valuemin={0}
        aria-valuemax={3}
        aria-valuenow={step.now}
        aria-valuetext={word}
        className="h-2 overflow-hidden rounded-pill bg-surface-1"
      >
        <span className={`block h-full rounded-pill bg-violet transition-[width] ${step.width}`} />
      </div>
      <p className="m-0 text-ui-sm text-violet-text" role="status">
        {word}
      </p>
    </div>
  );
}
