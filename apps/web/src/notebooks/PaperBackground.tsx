import { memo } from "react";
import { PAGE_HEIGHT, PAGE_WIDTH, PAPER_ROLE_TOKENS, type Paper, type PaperRole, paperPrimitives } from "./paper";

/** Each role's stroke/fill as a design token (CSS custom property), so the SVG has no colour of its own. */
const roleColor = (role: PaperRole) => `var(${PAPER_ROLE_TOKENS[role].token})`;

/**
 * The paper template as an SVG the size of the page (viewBox = US Letter in points), drawn from the same
 * primitives as the PDF export. Transparent apart from its lines, so the ink's marker layer (under the content)
 * shows through; the page colour is the surface's background. Presentation attributes only (no inline styles,
 * which the CSP refuses).
 */
export const PaperBackground = memo(function PaperBackground({
  paper,
  thumb = false,
}: {
  paper: Paper;
  /** A small preview (the paper picker): dots are drawn larger so the pattern stays visible. */
  thumb?: boolean;
}) {
  const prims = paperPrimitives(paper);
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox={`0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}`}
      preserveAspectRatio="none"
      className="pointer-events-none block h-auto w-full select-none"
      data-paper={paper}
    >
      {prims.map((p, i) => {
        const { opacity } = PAPER_ROLE_TOKENS[p.role];
        const key = `${p.kind}-${i}`;
        if (p.kind === "line")
          return (
            <line
              key={key}
              x1={p.x1}
              y1={p.y1}
              x2={p.x2}
              y2={p.y2}
              stroke={roleColor(p.role)}
              strokeOpacity={opacity}
              // Hairlines in screen px at any zoom (page-unit widths vanish on a phone-sized page).
              strokeWidth={p.width < 1 ? 1 : 1.5}
              vectorEffect="non-scaling-stroke"
            />
          );
        if (p.kind === "dot")
          return (
            <circle
              key={key}
              cx={p.x}
              cy={p.y}
              r={thumb ? p.r * 3.5 : p.r}
              fill={roleColor(p.role)}
              fillOpacity={opacity}
            />
          );
        return (
          <rect key={key} x={p.x} y={p.y} width={p.w} height={p.h} fill={roleColor(p.role)} fillOpacity={opacity} />
        );
      })}
    </svg>
  );
});
