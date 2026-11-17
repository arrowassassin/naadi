import type { WeekItemOut } from "@naadi/shared";
import { Button } from "@naadi/ui";
import { useUiText } from "../copy/useUiText";
import { Sheet } from "../learn/Sheet";
import { duration, itemTitle } from "../today/format";
import { useRunAction } from "../today/useRunAction";
import { ItemChips } from "./ItemBlock";
import { itemLink, type LinkContext } from "./itemLink";
import type { ItemStatus } from "./outbox";

/** An item opened: start it (deep link into Reader / Builder prefilled / Cards), mark it done, skip it. */
export function ItemSheet({
  item,
  pending,
  ctx,
  onClose,
  onStatus,
}: {
  item: WeekItemOut | null;
  pending: boolean;
  ctx: LinkContext;
  onClose: () => void;
  onStatus: (id: string, status: ItemStatus) => void;
}) {
  const t = useUiText();
  const { run, pending: starting, failed } = useRunAction();
  const action = item ? itemLink(item, ctx) : null;
  const done = item?.status === "done";
  const skipped = item?.status === "skipped";
  const isRest = item?.kind === "rest";
  return (
    <Sheet
      open={item !== null}
      onClose={onClose}
      title={item ? itemTitle(t, item) : ""}
      closeLabel={t("planner.sheet_close")}
    >
      {item ? (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-label-sm text-text-3 uppercase">{t(`today.kind_${item.kind}`)}</span>
            <ItemChips item={item} pending={pending} />
          </div>
          {item.target_minutes ? <p className="m-0 text-ui text-text-2">{duration(t, item.target_minutes)}</p> : null}
          {!action && !isRest ? (
            <p className="m-0 text-ui-sm text-text-3">
              {t(item.ref_type === "cards" ? "planner.sheet_cards_off" : "planner.sheet_no_link")}
            </p>
          ) : null}
          {failed ? (
            <p role="alert" className="m-0 text-ui text-amber">
              {t("today.cta_failed")}
            </p>
          ) : null}
          {isRest ? null : (
            <div className="flex flex-wrap gap-3">
              {action ? (
                <Button
                  variant="strong"
                  disabled={starting}
                  onClick={() => {
                    run(action);
                  }}
                >
                  {t("planner.sheet_start")}
                </Button>
              ) : null}
              <Button
                variant="secondary"
                onClick={() => {
                  onStatus(item.id, done ? "planned" : "done");
                  onClose();
                }}
              >
                {t(done ? "planner.sheet_undo" : "planner.sheet_done")}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  onStatus(item.id, skipped ? "planned" : "skipped");
                  onClose();
                }}
              >
                {t(skipped ? "planner.sheet_unskip" : "planner.sheet_skip")}
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </Sheet>
  );
}
