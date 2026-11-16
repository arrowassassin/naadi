import type { InkItem } from "@naadi/shared";
import { Button } from "@naadi/ui";
import { useEffect, useRef, useState } from "react";
import { InkCanvas } from "../ink/InkCanvas";
import { Sheet } from "../learn/Sheet";
import { useCopy } from "../practice/copy";
import { readScratch, writeScratch } from "./local";

type Engine = { getItems(): InkItem[]; clear?: () => void };

/**
 * Scratch paper for one exam question (surface `{block_id}:{n}`, kind exam_scratch): pen, marker and eraser on
 * a blank page. Kept on this device (Dexie) so it survives a reload and stays reviewable after the block.
 */
export function ScratchSheet({
  open,
  onClose,
  userId,
  blockId,
  n,
}: {
  open: boolean;
  onClose: () => void;
  userId: string;
  blockId: string;
  n: number;
}) {
  const t = useCopy();
  const [items, setItems] = useState<InkItem[] | null>(null);
  const engine = useRef<Engine | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setItems(null);
    void readScratch(userId, blockId, n).then((saved) => {
      if (live) setItems(saved);
    });
    return () => {
      live = false;
    };
  }, [open, userId, blockId, n]);

  const save = () => {
    const e = engine.current;
    if (e) void writeScratch(userId, blockId, n, e.getItems());
  };

  return (
    <Sheet open={open} onClose={onClose} title={t("exam.tool.scratch")} className="tablet:w-[min(52rem,94vw)]">
      <div className="flex flex-col gap-3 pb-2">
        <p className="m-0 text-text-2 text-ui-sm">{t("exam.scratch_note")}</p>
        {items === null ? (
          <div className="h-[50dvh] rounded-block border border-line bg-paper" aria-busy="true" />
        ) : (
          <InkCanvas
            items={items}
            enabled
            surfaceType="exam_scratch"
            theme="paper"
            label={t("exam.scratch_surface", { n })}
            className="rounded-block"
            engineRef={(e) => {
              engine.current = e as Engine | null;
            }}
            onStrokesChange={save}
          >
            <div className="h-[50dvh] min-h-[320px] rounded-block border border-line bg-paper" />
          </InkCanvas>
        )}
        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>
            {t("exam.close")}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
