/**
 * @naadi/ink: a framework-light ink engine any surface can mount (chapter section, question, flashcard,
 * PDF page, notebook page, exam scratch). No network calls. React binding: `@naadi/ink/react`.
 */
export * from "./anchors";
export {
  CanvasBudget,
  type CanvasLease,
  canvasBudget,
  DEFAULT_CANVAS_BUDGET,
  MAX_PARKED_BYTES,
  MAX_SLABS_PIXELS,
  parkedPictureBudget,
  slabBudget,
} from "./budget";
export * from "./coords";
export * from "./document";
export {
  DEFAULT_SIZES,
  type EraserMode,
  InkEngine,
  type InkEngineOptions,
  type InkEngineState,
  type InkError,
  type InkLabels,
  type InkSelectionPayload,
  type InkToolName,
  type MarkerMode,
  markerModeFor,
  type PxRect,
  type SelectionInfo,
  type ToolSizes,
} from "./engine";
export * from "./geometry";
export * from "./input";
export * from "./latency";
export { forgetPen, markPenPresent, onPenPresence, penPresent } from "./presence";
export { inkProbe, MainThreadProbe } from "./probe";
export * from "./render";
export * from "./shapes";
export { registerShortcutTarget, type ShortcutAction, shortcutFor } from "./shortcuts";
export * from "./spatial";
