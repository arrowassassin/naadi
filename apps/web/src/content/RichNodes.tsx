import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { adoptSvg, releaseDiagramStyles, renderMermaid } from "./mermaidHost";
import type { ContentEnv } from "./types";
import "./content.css";

/*
 * KaTeX and Mermaid load on first use only, so a chapter without them never pays for them. Neither puts an
 * inline style into the app's document (the CSP is style-src 'self'):
 * - KaTeX renders MathML only (`output: "mathml"`): MathML Core is native in Safari, Chrome and Firefox.
 * - Mermaid runs in a sandboxed iframe; see mermaidHost.ts.
 */

function RenderError({ message, detail, env }: { message: string; detail: string; env: ContentEnv }) {
  return (
    <div
      role="note"
      className="flex items-start gap-3 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text-2"
    >
      <AlertTriangle aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
      <span>
        {message}
        {env.mode === "dev" ? <span className="block font-mono text-label-sm text-text-3">{detail}</span> : null}
      </span>
    </div>
  );
}

export function MathBlock({ latex, env }: { latex: string; env: ContentEnv }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    let live = true;
    import("katex")
      .then(({ default: katex }) => {
        if (!live || !ref.current) return;
        katex.render(latex, ref.current, {
          output: "mathml",
          displayMode: true,
          throwOnError: true,
          trust: false,
          strict: "ignore",
          maxSize: 50,
          maxExpand: 500,
        });
        setDone(true);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [latex]);
  if (error) {
    return <RenderError message={env.text("content.math_failed")} detail={error} env={env} />;
  }
  return (
    <div data-node="math" className="overflow-x-auto py-2 text-center text-[1.15em] text-text">
      {/* KaTeX's MathML carries the formula's accessible text; until it renders the source stands in. */}
      <div ref={ref} aria-busy={done ? undefined : true}>
        <span className="sr-only">{latex}</span>
      </div>
    </div>
  );
}

export function MermaidBlock({ code, env }: { code: string; env: ContentEnv }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    let live = true;
    let styleId: string | null = null;
    setDone(false);
    setError(null);
    renderMermaid(code)
      .then(({ id, svg }) => {
        if (!live || !ref.current) return;
        adoptSvg(ref.current, svg, id);
        styleId = id;
        setDone(true);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
      if (styleId) releaseDiagramStyles(styleId);
    };
  }, [code]);
  if (error) {
    return <RenderError message={env.text("content.diagram_failed")} detail={error} env={env} />;
  }
  return (
    <figure data-node="mermaid" className="m-0 overflow-x-auto rounded-block border border-line bg-surface-2 p-4">
      <div
        ref={ref}
        role="img"
        aria-label={env.text("content.diagram_label")}
        aria-busy={done ? undefined : true}
        className="[&>svg]:mx-auto [&>svg]:max-w-full"
      />
      <details className="mt-3 text-ui-sm text-text-2">
        <summary className="cursor-pointer">{env.text("content.diagram_as_text")}</summary>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-label-sm">{code}</pre>
      </details>
    </figure>
  );
}
