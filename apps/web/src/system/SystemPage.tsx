import {
  AssistantBar,
  Button,
  Chip,
  ErrorState,
  Highlight,
  NavPill,
  OfflineBanner,
  Skeleton,
  TabBar,
  Tile,
  TileLabel,
} from "@naadi/ui";
import { BookOpen, CalendarDays, Layers, Sun } from "lucide-react";
import { type ReactNode, useState } from "react";
import { HealthPanel } from "../health/HealthPanel";
import {
  BREAKPOINTS,
  type ColorToken,
  MOTION,
  NEUTRAL_COLORS,
  RADII,
  RADIUS_RANGE,
  ROLE_COLORS,
  SPACING,
  TARGETS,
  TYPE_SCALE,
} from "./tokens";
import { useCssVars, usePrefersReducedMotion } from "./useCssVar";

/** Minifiers rewrite `200ms` as `.2s`; show durations in milliseconds as the tokens define them. */
function asMs(v: string): string {
  const m = /^(-?[\d.]+)(ms|s)$/.exec(v.trim());
  if (!m) return v;
  const n = Number(m[1]) * (m[2] === "s" ? 1000 : 1);
  return `${Math.round(n)} ms`;
}

function SectionHeading({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="m-0 font-mono text-label font-medium text-text-3 uppercase">
      {children}
    </h2>
  );
}

function Swatches({ tokens, label }: { tokens: ColorToken[]; label: string }) {
  const values = useCssVars(tokens.map((t) => t.cssVar));
  return (
    <ul
      aria-label={label}
      className="m-0 grid list-none grid-cols-2 gap-3 p-0 tablet:grid-cols-4 laptop:grid-cols-8"
      data-testid="swatches"
    >
      {tokens.map((t) => (
        <li key={t.cssVar} className="flex min-w-0 flex-col gap-2">
          <span
            aria-hidden="true"
            className="block h-[72px] rounded-swatch border border-line-strong"
            style={{ background: `var(${t.cssVar})` }}
          />
          <span className="text-[0.8125rem] font-semibold">{t.name}</span>
          <span className="font-mono text-label-sm break-words text-text-3">
            {(values[t.cssVar] ?? "").toUpperCase() || t.cssVar} · {t.use}
          </span>
        </li>
      ))}
    </ul>
  );
}

function TypeSection() {
  return (
    <section aria-labelledby="t-h" className="flex min-w-0 flex-col gap-3.5">
      <SectionHeading id="t-h">Type</SectionHeading>
      <ul className="m-0 flex list-none flex-col p-0">
        {TYPE_SCALE.map((t, i) => (
          <li
            key={t.label}
            className={
              i < TYPE_SCALE.length - 1
                ? "flex flex-col gap-1 border-b border-line-soft py-3.5 tablet:flex-row tablet:items-baseline tablet:gap-5"
                : "flex flex-col gap-1 py-3.5 tablet:flex-row tablet:items-baseline tablet:gap-5"
            }
          >
            <span className="w-[150px] flex-none font-mono text-label-sm text-text-3">{t.label}</span>
            <span className={`min-w-0 break-words ${t.className}`}>{t.sample}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ShapeSection() {
  const radii = useCssVars(RADIUS_RANGE);
  const space = useCssVars(SPACING);
  const targets = useCssVars(TARGETS.map((t) => t.cssVar));
  const motion = useCssVars(MOTION.map((m) => m.cssVar));
  const reduced = usePrefersReducedMotion();
  return (
    <section aria-labelledby="s-h" className="flex min-w-0 flex-col gap-3.5">
      <SectionHeading id="s-h">Radius · spacing · targets</SectionHeading>
      <ul className="m-0 grid list-none grid-cols-2 gap-3 p-0 tablet:grid-cols-4">
        {RADII.map((r) => (
          <li key={r.cssVar} className="flex flex-col gap-2">
            <span
              aria-hidden="true"
              className="block h-[88px] border border-line-strong bg-surface-2"
              style={{ borderRadius: `var(${r.cssVar})` }}
            />
            <span className="font-mono text-label-sm text-text-2">
              {(radii[r.cssVar] || r.cssVar).replace("px", "")} · {r.label}
            </span>
          </li>
        ))}
      </ul>
      <p className="m-0 font-mono text-label-sm text-text-3">
        Full range: {RADIUS_RANGE.map((v) => (radii[v] || v).replace("px", "")).join(" / ")}
      </p>

      <h3 className="m-0 mt-2 text-ui-sm font-semibold text-text-2">Spacing</h3>
      <ul className="m-0 flex list-none flex-wrap items-end gap-2.5 p-0" aria-label="Spacing scale">
        {SPACING.map((v) => (
          <li key={v} className="flex flex-col items-center gap-1.5">
            <span
              aria-hidden="true"
              className="block rounded-[3px] bg-text-2"
              style={{ width: `var(${v})`, height: `var(${v})` }}
            />
            <span className="font-mono text-label-sm text-text-2">{(space[v] || v).replace("px", "")}</span>
          </li>
        ))}
      </ul>

      <h3 className="m-0 mt-2 text-ui-sm font-semibold text-text-2">Touch targets</h3>
      <ul className="m-0 flex list-none flex-wrap items-end gap-3 p-0">
        {TARGETS.map((t) => (
          <li key={t.cssVar} className="flex flex-col items-start gap-1.5">
            <span
              aria-hidden="true"
              className="block rounded-pill border border-dashed border-line-dashed"
              style={{ height: `var(${t.cssVar})`, width: `calc(var(${t.cssVar}) * 2)` }}
            />
            <span className="font-mono text-label-sm text-text-2">
              {targets[t.cssVar] || t.cssVar} · {t.label}
            </span>
          </li>
        ))}
      </ul>

      <h3 className="m-0 mt-2 text-ui-sm font-semibold text-text-2">Breakpoints</h3>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-ui-sm">
        {BREAKPOINTS.map((b) => (
          <div key={b.name} className="contents">
            <dt className="font-mono text-label-sm text-text-3 uppercase">
              {b.name} {b.range}
            </dt>
            <dd className="m-0 text-text-2">{b.layout}</dd>
          </div>
        ))}
      </dl>

      <h3 className="m-0 mt-2 text-ui-sm font-semibold text-text-2">Motion</h3>
      <p className="m-0 text-ui-sm text-text-2">
        {MOTION.map((m) => `${asMs(motion[m.cssVar] || m.cssVar)} ${m.label}`).join(", ")}, ease-out. Reduced motion is{" "}
        <strong className="text-text">{reduced ? "on" : "off"}</strong> on this device
        {reduced ? ", so all motion is off." : "."} Focus ring: 3 px violet, 2 px offset.
      </p>
    </section>
  );
}

const NAV_ITEMS = [
  { id: "today", label: "Today" },
  { id: "learn", label: "Learn" },
  { id: "practice", label: "Practice" },
];

const TAB_ITEMS = [
  { id: "today", label: "Today", icon: <Sun size={20} /> },
  { id: "learn", label: "Learn", icon: <BookOpen size={20} /> },
  { id: "practice", label: "Practice", icon: <Layers size={20} /> },
  { id: "plan", label: "Plan", icon: <CalendarDays size={20} /> },
];

function ComponentsSection() {
  const [navCurrent, setNavCurrent] = useState("today");
  const [tabCurrent, setTabCurrent] = useState("today");
  const [specimenNote, setSpecimenNote] = useState("");
  const note = () => setSpecimenNote("Specimen only: the design-system page does not send messages to the assistant.");

  return (
    <section aria-labelledby="k-h" className="flex flex-col gap-4">
      <SectionHeading id="k-h">Components</SectionHeading>
      <div className="grid grid-cols-1 gap-5 tablet:grid-cols-2 laptop:grid-cols-3">
        <Tile className="flex flex-col gap-3.5">
          <h3 className="m-0 font-display text-[1.25rem] font-bold">Buttons</h3>
          <div className="flex flex-wrap gap-2.5">
            <Button variant="primary">Primary</Button>
            <Button variant="strong">Strong</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="assistant">Assistant</Button>
            <Button variant="secondary" disabled>
              Disabled
            </Button>
          </div>
          <p className="m-0 text-ui-sm text-text-3">
            Lime = the student's next step, one per view. Violet = anything the assistant does. Press Tab to see the
            focus ring.
          </p>
        </Tile>

        <Tile className="flex flex-col gap-3.5">
          <h3 className="m-0 font-display text-[1.25rem] font-bold">Navigation &amp; assistant bar</h3>
          <NavPill label="Navigation pill specimen" items={NAV_ITEMS} currentId={navCurrent} onSelect={setNavCurrent} />
          <AssistantBar onSubmit={note} onAttach={note} label="Assistant bar specimen" />
          <TabBar
            label="Phone tab bar specimen"
            items={TAB_ITEMS}
            currentId={tabCurrent}
            onSelect={setTabCurrent}
            onAssistant={note}
          />
          <p className="m-0 text-ui-sm text-text-3" aria-live="polite">
            {specimenNote ||
              "Floating pill at top on iPad and laptop, tab bar on phone, assistant bar pinned to the bottom of every page."}
          </p>
        </Tile>

        <Tile className="flex flex-col gap-3.5">
          <h3 className="m-0 font-display text-[1.25rem] font-bold">Highlights &amp; status</h3>
          <p className="m-0 text-[1rem] leading-[1.8]">
            <Highlight kind="key-fact">Key fact</Highlight> · <Highlight kind="formula">Formula</Highlight> ·{" "}
            <Highlight kind="mistake">My mistake</Highlight>
          </p>
          <div className="flex flex-wrap gap-2">
            <Chip tone="now">Now</Chip>
            <Chip tone="done">Done</Chip>
            <Chip tone="planned">Planned</Chip>
            <Chip tone="weak">Weak area</Chip>
            <Chip tone="meta">4K HDR</Chip>
          </div>
          <p className="m-0 text-ui-sm text-text-3">
            Each highlight colour has a fixed meaning, so Notes can filter by it. Status never relies on colour alone:
            every chip carries a word.
          </p>
        </Tile>

        <Tile className="flex flex-col gap-2.5">
          <TileLabel>Tile · default</TileLabel>
          <span className="font-display text-[1.75rem] font-bold tracking-[-0.02em]">Today's flow</span>
          <span className="text-ui-sm text-text-2">Surface 1, 1 px line, 32 radius, 32 padding.</span>
        </Tile>
        <Tile variant="assistant" className="flex flex-col gap-2.5">
          <TileLabel variant="assistant">Tile · assistant</TileLabel>
          <span className="font-display text-[1.5rem] leading-[1.2]">Naadi noticed something.</span>
          <span className="text-ui-sm text-violet-text">Violet ground, used only for AI-written content.</span>
        </Tile>
        <Tile variant="paper" className="flex flex-col gap-2.5">
          <TileLabel variant="paper">Tile · paper</TileLabel>
          <span className="font-display text-[1.75rem] font-extrabold tracking-[-0.02em]">Her own notes</span>
          <span className="text-ui-sm text-paper-text-2">
            Light ground, used only for what the student writes or draws.
          </span>
        </Tile>

        <Tile className="flex flex-col gap-3.5 tablet:col-span-2 laptop:col-span-3">
          <h3 className="m-0 font-display text-[1.25rem] font-bold">States</h3>
          <div className="grid grid-cols-1 gap-4 laptop:grid-cols-3">
            <div className="flex flex-col gap-2" aria-label="Loading skeleton specimen" role="img">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
            <ErrorState
              live={false}
              title="Error state"
              message="A short reason, what still works, and one way to recover."
              requestId="0c9f1e7a2b6d4e3f8a1b2c3d4e5f6a7b"
            />
            <OfflineBanner live={false} message="Showing the last saved result." className="self-start" />
          </div>
          <p className="m-0 text-ui-sm text-text-3">
            Loading shows skeletons, errors say what happened with a retry, and offline keeps the last result visible.
            The health panel above uses all three for real.
          </p>
        </Tile>
      </div>
    </section>
  );
}

export function SystemPage() {
  return (
    <main id="main" className="mx-auto flex max-w-(--content-max) flex-col gap-12 px-(--gutter) py-10 tablet:py-16">
      <header className="flex flex-col gap-6 laptop:flex-row laptop:items-end laptop:justify-between laptop:gap-8">
        <h1 className="m-0 font-display text-[clamp(3.5rem,2rem+7vw,7.5rem)] leading-[0.86] font-extrabold tracking-[-0.05em]">
          Naadi<span className="text-lime">.</span> <span className="text-text-muted">system</span>
        </h1>
        <p className="m-0 max-w-[380px] text-[1rem] text-text-2">
          One black ground, one lime for the student's actions, one violet for the assistant. Everything else is
          neutral.
        </p>
      </header>

      <HealthPanel />

      <section aria-labelledby="c-h" className="flex flex-col gap-4">
        <SectionHeading id="c-h">Color tokens</SectionHeading>
        <Swatches tokens={ROLE_COLORS} label="Role colours" />
        <h3 className="m-0 mt-2 text-ui-sm font-semibold text-text-2">Neutrals and lines</h3>
        <Swatches tokens={NEUTRAL_COLORS} label="Neutral colours" />
      </section>

      <div className="grid grid-cols-1 gap-12 laptop:grid-cols-[1.2fr_0.8fr]">
        <TypeSection />
        <ShapeSection />
      </div>

      <ComponentsSection />
    </main>
  );
}
