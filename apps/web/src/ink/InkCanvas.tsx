import { InkSurface, type InkSurfaceProps } from "@naadi/ink/react";
import { useState } from "react";
import { usePenSurface } from "../shell/chrome";

/**
 * The app's ink surface: `@naadi/ink`'s InkSurface plus the shell's pen-surface registration, so the
 * shell keeps its chrome out of the way while ink is on (contract S03/S12a). Use this, not InkSurface,
 * inside the app.
 */
export function InkCanvas(props: InkSurfaceProps) {
  const [penActive, setPenActive] = useState(false);
  usePenSurface(penActive);
  return (
    <InkSurface
      {...props}
      onPenActiveChange={(active) => {
        setPenActive(active);
        props.onPenActiveChange?.(active);
      }}
    />
  );
}
