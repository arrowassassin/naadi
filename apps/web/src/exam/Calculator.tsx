import { cx } from "@naadi/ui";
import { useState } from "react";
import { Sheet } from "../learn/Sheet";
import { useCopy } from "../practice/copy";
import { type CalcKey, INITIAL, keyToCalc, press } from "./calc";

const KEY =
  "flex min-h-14 items-center justify-center rounded-block border border-line bg-surface-2 font-mono text-[1.25rem] text-text hover:border-line-control active:bg-surface-1";

/** Four-function calculator in a sheet. Its keys work only while it is open (the sheet is a modal dialog). */
export function Calculator({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useCopy();
  const [state, setState] = useState(INITIAL);
  const hit = (k: CalcKey) => setState((s) => press(s, k));
  const digit = (d: string) => ({ digit: d });
  const rows: { key: CalcKey; label: string; name: string; wide?: boolean }[][] = [
    [
      { key: "C", label: "C", name: t("exam.calc.clear") },
      { key: "CE", label: "CE", name: t("exam.calc.clear_entry") },
      { key: "sign", label: "±", name: t("exam.calc.sign") },
      { key: { op: "/" }, label: "÷", name: t("exam.calc.divide") },
    ],
    [
      { key: digit("7"), label: "7", name: "7" },
      { key: digit("8"), label: "8", name: "8" },
      { key: digit("9"), label: "9", name: "9" },
      { key: { op: "*" }, label: "×", name: t("exam.calc.multiply") },
    ],
    [
      { key: digit("4"), label: "4", name: "4" },
      { key: digit("5"), label: "5", name: "5" },
      { key: digit("6"), label: "6", name: "6" },
      { key: { op: "-" }, label: "−", name: t("exam.calc.subtract") },
    ],
    [
      { key: digit("1"), label: "1", name: "1" },
      { key: digit("2"), label: "2", name: "2" },
      { key: digit("3"), label: "3", name: "3" },
      { key: { op: "+" }, label: "+", name: t("exam.calc.add") },
    ],
    [
      { key: digit("0"), label: "0", name: "0", wide: true },
      { key: ".", label: ".", name: t("exam.calc.point") },
      { key: "=", label: "=", name: t("exam.calc.equals") },
    ],
  ];
  return (
    <Sheet open={open} onClose={onClose} title={t("exam.tool.calculator")} className="tablet:w-[min(24rem,92vw)]">
      {/* biome-ignore lint/a11y/noStaticElementInteractions: keyboard entry for the calculator grid inside the dialog */}
      <div
        className="flex flex-col gap-3 pb-2"
        onKeyDown={(e) => {
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          if (e.key === "Escape") return; // the dialog closes
          const k = keyToCalc(e.key);
          if (!k) return;
          e.preventDefault();
          hit(k);
        }}
      >
        <output
          aria-live="polite"
          aria-label={t("exam.calc.display")}
          className={cx(
            "block min-h-16 overflow-x-auto rounded-block border border-line bg-ground px-4 py-3 text-right font-mono text-[2rem] tabular-nums",
            state.error && "text-amber",
          )}
        >
          {state.error ? t("exam.calc.error") : state.display}
        </output>
        <div className="grid grid-cols-4 gap-2">
          {rows.flat().map((b) => (
            <button
              key={b.label}
              type="button"
              aria-label={b.name}
              onClick={() => hit(b.key)}
              className={cx(KEY, b.wide && "col-span-2")}
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>
    </Sheet>
  );
}
