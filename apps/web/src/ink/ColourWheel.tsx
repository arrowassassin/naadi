import { Button } from "@naadi/ui";
import { useId, useRef, useState } from "react";
import { useInkCopy } from "./copy";

function hsv(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
  const v = Math.max(r, g, b),
    delta = v - Math.min(r, g, b);
  const turns = delta === 0 ? 0 : v === r ? (g - b) / delta : v === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return { h: (turns * 60 + 360) % 360, s: v === 0 ? 0 : delta / v, v };
}
function hex(h: number, s: number, v: number) {
  const channel = (n: number) => {
    const k = (n + h / 60) % 6;
    return Math.round(255 * v * (1 - s * Math.max(0, Math.min(k, 4 - k, 1))))
      .toString(16)
      .padStart(2, "0");
  };
  return "#" + channel(5) + channel(3) + channel(1);
}

export function ColourWheel({
  color,
  onColor,
  disabled,
}: {
  color: string;
  onColor: (color: string) => void;
  disabled: boolean;
}) {
  const t = useInkCopy();
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [pick, setPick] = useState({ h: 0, s: 1, v: 1 });
  const value = hex(pick.h, pick.s, pick.v);
  function select(e: React.PointerEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * 2 - 1,
      y = ((e.clientY - box.top) / box.height) * 2 - 1;
    setPick({ ...pick, h: ((Math.atan2(x, -y) * 180) / Math.PI + 360) % 360, s: Math.min(1, Math.hypot(x, y)) });
  }
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        className="ink-wheel-trigger size-11 rounded-full border-[3px] border-text-3 disabled:opacity-50"
        aria-haspopup="dialog"
        aria-label={t("ink.colour.wheel")}
        onClick={() => {
          if (color.startsWith("#")) setPick(hsv(color));
          dialog.current?.showModal();
        }}
      >
        {color.startsWith("#") && (
          <span className="inline-block size-5 rounded-full border border-white" style={{ backgroundColor: color }} />
        )}
      </button>
      <dialog
        ref={dialog}
        aria-labelledby={id}
        className="m-auto max-h-[90dvh] w-[min(19rem,94vw)] overflow-auto rounded-tile border border-line bg-surface-1 p-5 text-text backdrop:bg-ground/70"
      >
        <h2 id={id} className="mb-4 font-display text-xl">
          {t("ink.colour.wheel")}
        </h2>
        <div
          className="ink-colour-disc relative mx-auto aspect-square w-full touch-none rounded-full"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            select(e);
          }}
          onPointerMove={(e) => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) select(e);
          }}
        >
          <div className="pointer-events-none absolute inset-0 rounded-full bg-black" style={{ opacity: 1 - pick.v }} />
          <span
            className="pointer-events-none absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow"
            style={{
              left: 50 + 50 * pick.s * Math.sin((pick.h * Math.PI) / 180) + "%",
              top: 50 - 50 * pick.s * Math.cos((pick.h * Math.PI) / 180) + "%",
              backgroundColor: value,
            }}
          />
        </div>
        <label className="my-4 flex flex-col gap-2">
          {t("ink.colour.brightness")}
          <input
            type="range"
            min="0"
            max="100"
            value={Math.round(pick.v * 100)}
            onChange={(e) => setPick({ ...pick, v: Number(e.target.value) / 100 })}
          />
        </label>
        <label className="mb-4 flex items-center justify-between gap-2">
          {t("ink.colour.exact")}
          <input type="color" value={value} onChange={(e) => setPick(hsv(e.target.value))} />
        </label>
        <Button
          onClick={() => {
            onColor(value);
            dialog.current?.close();
          }}
        >
          {t("ink.colour.done")}
        </Button>
      </dialog>
    </>
  );
}
