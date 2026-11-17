import { useMemo } from "react";
import { MermaidBlock } from "../../content/RichNodes";
import type { ContentEnv } from "../../content/types";
import { useUiText } from "../../copy/useUiText";
import type { CanvasKindProps } from "../registry";
import type { DiagramData } from "./index";

/** A Mermaid diagram through the app's sandboxed renderer (error state if it fails to render). */
export default function DiagramKind({ item }: CanvasKindProps<DiagramData>) {
  const t = useUiText();
  const env = useMemo<ContentEnv>(
    () => ({
      media: {},
      questions: {},
      mode: "prod",
      sectionId: `canvas-${item.key}`,
      headingBase: 3,
      minHeading: 1,
      embedOrigin: window.location.origin,
      text: (k, v) => t(k, v),
    }),
    [t, item.key],
  );
  return <MermaidBlock code={item.data.mermaid} env={env} />;
}
