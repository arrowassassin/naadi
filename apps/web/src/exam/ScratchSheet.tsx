import { Button } from "@naadi/ui";
import { InkCanvas } from "../ink/InkCanvas";
import { InkSaveStatus } from "../ink/persist/InkSaveStatus";
import { useInkSettings } from "../ink/persist/settings";
import { useInkLayer } from "../ink/persist/useInkLayer";
import { Sheet } from "../learn/Sheet";
import { useCopy } from "../practice/copy";

/**
 * Scratch paper for one exam question: an ink surface of kind `exam_scratch`, id `{block_id}:{n}`, saved and
 * synced through the ink API (segment 12b), so it survives a reload and is reviewable after the block.
 */
function Scratch({ blockId, n }: { blockId: string; n: number }) {
  const t = useCopy();
  const surfaceId = `${blockId}:${n}`;
  const layer = useInkLayer("exam_scratch", surfaceId);
  const { settings } = useInkSettings();
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="m-0 text-text-2 text-ui-sm">{t("exam.scratch_note")}</p>
        <InkSaveStatus layer={layer} />
      </div>
      <InkCanvas
        key={surfaceId}
        {...layer.canvasProps}
        enabled
        surfaceType="exam_scratch"
        theme="paper"
        penOnly={settings.pen_only}
        predict={settings.predict}
        label={t("exam.scratch_surface", { n })}
        className="rounded-block"
      >
        <div className="h-[50dvh] min-h-[320px] rounded-block border border-line bg-paper" />
      </InkCanvas>
    </>
  );
}

export function ScratchSheet({
  open,
  onClose,
  blockId,
  n,
}: {
  open: boolean;
  onClose: () => void;
  blockId: string;
  n: number;
}) {
  const t = useCopy();
  return (
    <Sheet open={open} onClose={onClose} title={t("exam.tool.scratch")} className="tablet:w-[min(52rem,94vw)]">
      <div className="flex flex-col gap-3 pb-2">
        {open ? <Scratch blockId={blockId} n={n} /> : null}
        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>
            {t("exam.close")}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
