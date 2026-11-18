import { DataTableView } from "../../assistant/blocks";
import { useCanvasTitle } from "../CanvasView";
import type { CanvasKindProps } from "../registry";
import type { CanvasTableData } from "./index";

/** A table the assistant built (rows as data, labels from ui_copy keys or plain text). */
export default function TableKind({ item }: CanvasKindProps<CanvasTableData>) {
  const title = useCanvasTitle();
  return <DataTableView data={item.data} caption={title(item)} />;
}
