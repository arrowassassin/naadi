import type { RebalanceOut } from "@naadi/shared";
import { Button, TileLabel } from "@naadi/ui";
import { Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { counted, dayMonth } from "./format";
import type { Decision } from "./runs";

/** Violet: the assistant proposes. Nothing changes until Accept; Show changes lists every move, drop and warning. */
export function RebalanceCard({
  row,
  busy,
  offline,
  onDecide,
}: {
  row: RebalanceOut;
  busy: boolean;
  offline: boolean;
  onDecide: (decision: Decision) => void;
}) {
  const t = useUiText();
  const [open, setOpen] = useState(false);
  const listId = useId();
  const p = row.proposal;
  const unplaced = p.warnings.filter((w) => w.code === "unplaced").reduce((n, w) => n + w.count, 0);
  const disabled = busy || offline;
  return (
    <section
      aria-labelledby={`${listId}-title`}
      data-testid="rebalance-card"
      className="flex flex-col gap-4 rounded-tile-sm border border-violet-line bg-violet-ground p-6 text-text tablet:rounded-tile tablet:p-7"
    >
      <TileLabel variant="assistant" className="flex items-center gap-2">
        <Sparkles aria-hidden="true" size={14} />
        {t("planner.suggest_label")}
      </TileLabel>
      <h2 id={`${listId}-title`} className="m-0 font-display font-bold text-[1.375rem] leading-tight">
        {counted(t, "planner.suggest_title", p.missed)}
      </h2>
      <p className="m-0 text-ui text-violet-text">{t("planner.suggest_body")}</p>
      <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-ui text-text">
        {p.moves.length > 0 ? <li>{counted(t, "planner.sum_moves", p.moves.length)}</li> : null}
        {p.drops.length > 0 ? <li>{counted(t, "planner.sum_drops", p.drops.length)}</li> : null}
        {unplaced > 0 ? <li className="text-amber">{counted(t, "planner.sum_unplaced", unplaced)}</li> : null}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="assistant" disabled={disabled} onClick={() => onDecide("accept")}>
          {busy ? t("planner.working") : t("planner.accept")}
        </Button>
        <Button variant="secondary" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((v) => !v)}>
          {t(open ? "planner.hide_changes" : "planner.show_changes")}
        </Button>
        <Button variant="secondary" disabled={disabled} onClick={() => onDecide("decline")}>
          {t("planner.decline")}
        </Button>
      </div>
      {offline ? <p className="m-0 text-ui-sm text-text-3">{t("planner.rebalance_offline")}</p> : null}
      <div id={listId} hidden={!open} className="flex flex-col gap-4 border-violet-line border-t pt-4">
        {open ? (
          <>
            <h3 className="m-0 font-mono text-label text-violet-text uppercase tracking-[0.12em]">
              {t("planner.diff_title")}
            </h3>
            {p.moves.length > 0 ? (
              <div className="flex flex-col gap-2">
                <h4 className="m-0 text-ui font-semibold">{t("planner.diff_moves")}</h4>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-ui text-text-2" data-testid="diff-moves">
                  {p.moves.map((m) => (
                    <li key={m.item_id}>
                      {t("planner.diff_move", { title: m.title, from: dayMonth(m.from_day), to: dayMonth(m.to_day) })}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {p.drops.length > 0 ? (
              <div className="flex flex-col gap-2">
                <h4 className="m-0 text-ui font-semibold">{t("planner.diff_drops")}</h4>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-ui text-text-2" data-testid="diff-drops">
                  {p.drops.map((d) => (
                    <li key={d.item_id}>{t("planner.diff_drop", { title: d.title, day: dayMonth(d.day) })}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {p.warnings.length > 0 ? (
              <div className="flex flex-col gap-2">
                <h4 className="m-0 text-ui font-semibold">{t("planner.diff_warnings")}</h4>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-ui text-amber" data-testid="diff-warnings">
                  {p.warnings.map((w) => (
                    <li key={w.code}>
                      {w.code === "no_study_days"
                        ? t("planner.warn_no_study_days")
                        : counted(t, `planner.warn_${w.code}`, w.count)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
