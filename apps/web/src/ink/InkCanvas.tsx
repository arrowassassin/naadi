import { InkSurface, type InkSurfaceProps } from "@naadi/ink/react";
import { useState } from "react";
import { usePenSurface } from "../shell/chrome";
import { inkLabels, useInkCopy } from "./copy";

export type InkCanvasProps = Omit<InkSurfaceProps, "labels">;

/**
 * The app's ink surface: `@naadi/ink`'s InkSurface plus the shell's pen-surface registration, so the
 * shell keeps its chrome out of the way while ink is on (contract S03/S12a), and the engine's accessible
 * names from ui_copy. Use this, not InkSurface, inside the app.
 */
export function InkCanvas(props: InkCanvasProps) {
  const [penActive, setPenActive] = useState(false);
  const t = useInkCopy();
  usePenSurface(penActive);
  return (
    <InkSurface
      {...props}
      labels={inkLabels(t)}
      onPenActiveChange={(active) => {
        setPenActive(active);
        props.onPenActiveChange?.(active);
      }}
    />
  );
}
