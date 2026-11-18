import { ChartView } from "../../assistant/blocks";
import { useCanvasTitle } from "../CanvasView";
import type { CanvasKindProps } from "../registry";
import type { CanvasChartData } from "./index";

/** A bar or line chart (in-house SVG on the tokens, with its data table). */
export default function ChartKind({ item }: CanvasKindProps<CanvasChartData>) {
  const title = useCanvasTitle();
  return <ChartView data={{ ...item.data, unit: item.data.unit ?? null }} title={title(item)} />;
}
