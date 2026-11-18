/**
 * The design-system reference: which tokens the sheet displays, by CSS custom property name, with
 * their role. Values are read from the live CSS (packages/ui/src/tokens.css) at runtime, so the sheet
 * can never disagree with what the app actually renders.
 */

export interface ColorToken {
  name: string;
  cssVar: `--color-${string}`;
  use: string;
}

export const ROLE_COLORS: ColorToken[] = [
  { name: "ground", cssVar: "--color-ground", use: "page" },
  { name: "surface-1", cssVar: "--color-surface-1", use: "tiles" },
  { name: "surface-2", cssVar: "--color-surface-2", use: "inner" },
  { name: "line", cssVar: "--color-line", use: "borders" },
  { name: "text", cssVar: "--color-text", use: "primary" },
  { name: "text-2", cssVar: "--color-text-2", use: "body" },
  { name: "text-3", cssVar: "--color-text-3", use: "labels" },
  { name: "paper", cssVar: "--color-paper", use: "notes" },
  { name: "lime", cssVar: "--color-lime", use: "action" },
  { name: "lime-ink", cssVar: "--color-lime-ink", use: "on lime" },
  { name: "violet", cssVar: "--color-violet", use: "assistant" },
  { name: "violet-ground", cssVar: "--color-violet-ground", use: "AI tiles" },
  { name: "violet-text", cssVar: "--color-violet-text", use: "on violet" },
  { name: "amber", cssVar: "--color-amber", use: "weak / wrong" },
  { name: "mark-amber", cssVar: "--color-mark-amber", use: "highlight" },
  { name: "ink-violet", cssVar: "--color-ink-violet", use: "pen on paper" },
];

export const NEUTRAL_COLORS: ColorToken[] = [
  { name: "bar", cssVar: "--color-bar", use: "nav pill, assistant bar" },
  { name: "control", cssVar: "--color-control", use: "round icon controls" },
  { name: "line-soft", cssVar: "--color-line-soft", use: "tile borders, rules" },
  { name: "line-chip", cssVar: "--color-line-chip", use: "meta chips" },
  { name: "line-strong", cssVar: "--color-line-strong", use: "swatches, inputs" },
  { name: "line-control", cssVar: "--color-line-control", use: "secondary buttons" },
  { name: "line-dashed", cssVar: "--color-line-dashed", use: "planned, disabled" },
  { name: "text-muted", cssVar: "--color-text-muted", use: "disabled text" },
  { name: "paper-text-2", cssVar: "--color-paper-text-2", use: "secondary on paper" },
  { name: "violet-line", cssVar: "--color-violet-line", use: "AI tile border" },
  { name: "violet-ink", cssVar: "--color-violet-ink", use: "on violet button" },
  { name: "lime-hover", cssVar: "--color-lime-hover", use: "link hover" },
];

export interface TypeToken {
  label: string;
  className: string;
  sample: string;
}

export const TYPE_SCALE: TypeToken[] = [
  {
    label: "display · 800 · 72–216",
    // The token scales to 216 px on wide screens; the specimen is capped so it fits its column.
    className: "font-display text-[clamp(3.5rem,2rem+5vw,5.5rem)] leading-[0.9] tracking-[-0.05em] font-extrabold",
    sample: "38 days.",
  },
  {
    label: "headline · 700 · 28–60",
    className: "font-display text-headline font-bold",
    sample: "Acid–base, step by step.",
  },
  {
    label: "lead · 400 · 24–38",
    className: "font-display text-lead",
    sample: "Today is renal day. One lesson, forty questions.",
  },
  {
    label: "reading · Inter 400 · 17–20",
    className: "font-sans text-reading text-text-2",
    sample: "A raised gap means an unmeasured acid has been added.",
  },
  {
    label: "label · Mono 500 · 11–13",
    className: "font-mono text-label font-medium uppercase",
    sample: "Renal · Lesson 6 of 14",
  },
  {
    label: "ink · Caveat 700",
    className: "font-ink text-ink font-bold",
    sample: "Handwritten notes preview",
  },
];

export const RADII = [
  { cssVar: "--radius-inner", label: "inner" },
  { cssVar: "--radius-block", label: "block" },
  { cssVar: "--radius-tile", label: "tile" },
  { cssVar: "--radius-pill", label: "pill · controls" },
] as const;

export const RADIUS_RANGE = [
  "--radius-inner",
  "--radius-block-sm",
  "--radius-block",
  "--radius-tile-sm",
  "--radius-tile",
  "--radius-sheet-sm",
  "--radius-sheet",
  "--radius-pill",
] as const;

export const SPACING = [
  "--space-4",
  "--space-8",
  "--space-12",
  "--space-16",
  "--space-20",
  "--space-24",
  "--space-32",
  "--space-48",
  "--space-64",
] as const;

export const TARGETS = [
  { cssVar: "--target-min", label: "minimum" },
  { cssVar: "--target-assistant", label: "assistant bar" },
  { cssVar: "--target-cta", label: "primary CTA" },
  { cssVar: "--target-cta-lg", label: "large CTA" },
] as const;

export const BREAKPOINTS = [
  { name: "phone", range: "< 640 px", layout: "bottom tab bar, assistant sheet" },
  { name: "tablet", range: "640–1100 px", layout: "nav pill, two columns" },
  { name: "laptop", range: "≥ 1100 px", layout: "nav pill, up to four columns" },
] as const;

export const MOTION = [
  { cssVar: "--duration-control", label: "controls" },
  { cssVar: "--duration-sheet", label: "sheets" },
] as const;
