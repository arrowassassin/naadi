import { AlertTriangle } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ContentEnv } from "./types";

/*
 * KaTeX and Mermaid load on first use only (their own chunks), so a chapter without them never pays for them.
 * Both are rendered without inline styles, which the CSP (style-src 'self') would block:
 * - KaTeX renders MathML only (`output: "mathml"`): MathML Core is native in Safari, Chrome and Firefox.
 * - Mermaid's SVG is parsed in an inert document; its <style> rules move into a constructed stylesheet
 *   (CSSOM, allowed by CSP) and each `style` attribute is re-applied through `element.style`.
 */

function RenderError({ what, detail, env }: { what: string; detail: string; env: ContentEnv }) {
  return (
    <div
      role="note"
      className="flex items-start gap-3 rounded-block border border-amber/60 bg-surface-2 px-4 py-3 text-text-2"
    >
      <AlertTriangle aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
      <span>
        {what} could not be displayed.
        {env.mode === "dev" ? <span className="block font-mono text-label-sm text-text-3">{detail}</span> : null}
      </span>
    </div>
  );
}

export function MathBlock({ latex, env }: { latex: string; env: ContentEnv }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
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
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [latex]);
  if (error) return <RenderError what="This formula" detail={error} env={env} />;
  return (
    <div data-node="math" className="overflow-x-auto py-2 text-center text-[1.15em] text-text">
      <div ref={ref} aria-busy={ref.current?.childElementCount ? undefined : true}>
        <span className="sr-only">{latex}</span>
      </div>
    </div>
  );
}

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid() {
  mermaidReady ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "dark",
      fontFamily: "Inter Variable, Inter, system-ui, sans-serif",
      flowchart: { htmlLabels: false },
      htmlLabels: false,
    });
    return mermaid;
  });
  return mermaidReady;
}

/** Move an SVG string into `host` without inline styles (see the note at the top of this file). */
export function adoptSvg(host: HTMLElement, svgText: string): void {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.nodeName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) {
    throw new Error("Diagram output is not valid SVG");
  }
  const css: string[] = [];
  for (const s of Array.from(svg.querySelectorAll("style"))) {
    css.push(s.textContent ?? "");
    s.remove();
  }
  for (const bad of Array.from(svg.querySelectorAll("script, foreignObject"))) bad.remove();
  const styled: Array<[string, string]> = [];
  for (const el of [svg, ...Array.from(svg.querySelectorAll("[style]"))]) {
    const st = el.getAttribute("style");
    if (st) {
      const marker = `m${styled.length}`;
      el.setAttribute("data-style-ref", marker);
      styled.push([marker, st]);
      el.removeAttribute("style");
    }
  }
  const node = document.importNode(svg, true) as unknown as SVGSVGElement;
  host.replaceChildren(node);
  for (const [marker, st] of styled) {
    const el = host.querySelector<SVGElement>(`[data-style-ref="${marker}"]`);
    if (!el) continue;
    el.removeAttribute("data-style-ref");
    for (const decl of st.split(";")) {
      const i = decl.indexOf(":");
      if (i > 0) el.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim());
    }
  }
  if (css.length && "adoptedStyleSheets" in document) {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css.join("\n"));
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }
}

export function MermaidBlock({ code, env }: { code: string; env: ContentEnv }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = `mmd-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    let live = true;
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
      .then(({ svg }) => {
        if (!live || !ref.current) return;
        adoptSvg(ref.current, svg);
        setDone(true);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [code, id]);
  if (error) return <RenderError what="This diagram" detail={error} env={env} />;
  return (
    <figure data-node="mermaid" className="m-0 overflow-x-auto rounded-block border border-line bg-surface-2 p-4">
      <div ref={ref} role="img" aria-label="Diagram" aria-busy={!done || undefined} className="[&>svg]:mx-auto" />
      <details className="mt-3 text-ui-sm text-text-2">
        <summary className="cursor-pointer">Diagram as text</summary>
        <pre className="mt-2 overflow-x-auto font-mono text-label-sm whitespace-pre-wrap">{code}</pre>
      </details>
    </figure>
  );
}
