import type { InkError, InkLabels } from "@naadi/ink";
import { useUiCopy } from "../agent/uiCopy";

export type InkCopy = (key: string, vars?: Record<string, string | number>) => string;

/**
 * `t("ink.tool.pen")`: the configured string (app_config.ui_copy, editable without a redeploy), else the
 * shipped default (packages/shared/ui-copy.defaults.json, generated from config/app_config.yaml). Every ink
 * string the student sees comes through here; none is written in the components.
 */
export function useInkCopy(): InkCopy {
  const copy = useUiCopy();
  return (key, vars = {}) => copy(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))) ?? key;
}

export function inkLabels(t: InkCopy): InkLabels {
  return { newTextBox: t("ink.text.new"), editTextBox: t("ink.text.edit") };
}

export function inkErrorText(t: InkCopy, error: InkError): string {
  if (error.code === "too_many_items") return t("ink.error.too_many", { max: error.max.toLocaleString() });
  return t(error.code === "no_canvas" ? "ink.error.no_canvas" : "ink.error.canvas_memory");
}
