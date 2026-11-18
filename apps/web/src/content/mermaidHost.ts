import DOMPurify from "dompurify";

/*
 * Mermaid diagrams under the app's strict CSP (style-src 'self', no inline styles).
 *
 * Mermaid lays out SVG text with inline styles, so it runs in ONE hidden `<iframe sandbox="allow-scripts">`
 * (sandbox/mermaid.html: opaque origin, no cookies or storage, its own CSP that allows inline styles only
 * there). The app posts the code, the frame posts the SVG back. The SVG is untrusted: it is sanitised with
 * DOMPurify's SVG profile, its <style> rules go into ONE shared constructed stylesheet (CSSOM, allowed by CSP;
 * each diagram's rules are removed when it unmounts, so nothing accumulates), and every `style` attribute is
 * re-applied through `element.style` (also CSSOM).
 */

const SANDBOX_URL = "/sandbox/mermaid.html";
const RENDER_TIMEOUT_MS = 20_000;

type Pending = { resolve: (svg: string) => void; reject: (e: Error) => void; timer: number };

let frame: HTMLIFrameElement | null = null;
let ready: Promise<void> | null = null;
const pending = new Map<string, Pending>();
let counter = 0;

function onMessage(event: MessageEvent): void {
  if (!frame || event.source !== frame.contentWindow) return;
  const data = event.data as { type?: unknown; id?: unknown; svg?: unknown; error?: unknown } | null;
  if (data?.type !== "naadi:mermaid:result" || typeof data.id !== "string") return;
  const p = pending.get(data.id);
  if (!p) return;
  pending.delete(data.id);
  window.clearTimeout(p.timer);
  if (typeof data.svg === "string") p.resolve(data.svg);
  else p.reject(new Error(typeof data.error === "string" ? data.error : "The diagram could not be rendered."));
}

function ensureFrame(): Promise<void> {
  if (ready) return ready;
  ready = new Promise<void>((resolve, reject) => {
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("aria-hidden", "true");
    f.setAttribute("tabindex", "-1");
    f.title = "Diagram renderer";
    f.className = "naadi-mermaid-sandbox";
    const timer = window.setTimeout(() => reject(new Error("The diagram renderer did not start.")), RENDER_TIMEOUT_MS);
    const onReady = (event: MessageEvent) => {
      if (event.source !== f.contentWindow) return;
      if ((event.data as { type?: unknown } | null)?.type !== "naadi:mermaid:ready") return;
      window.removeEventListener("message", onReady);
      window.clearTimeout(timer);
      resolve();
    };
    window.addEventListener("message", onReady);
    window.addEventListener("message", onMessage);
    f.src = SANDBOX_URL;
    document.body.append(f);
    frame = f;
  });
  ready.catch(() => {
    ready = null;
    frame?.remove();
    frame = null;
  });
  return ready;
}

/**
 * Mermaid drops `<` and `>` from labels in strict mode; write them as its entity codes (`#lt;` / `#gt;`) inside
 * node and edge labels only, so arrows (`-->`, `==>`) and shape openers are untouched and "Gap > 12?" stays.
 */
export function escapeMermaidLabels(code: string): string {
  const esc = (s: string) => s.replace(/</g, "#lt;").replace(/>/g, "#gt;");
  return code
    .replace(/"([^"\n]*)"/g, (_m, inner: string) => `"${esc(inner)}"`)
    .replace(/\[([^[\]\n]*)\]/g, (_m, inner: string) => `[${esc(inner)}]`)
    .replace(/\{([^{}\n]*)\}/g, (_m, inner: string) => `{${esc(inner)}}`)
    .replace(/\(([^()\n]*)\)/g, (_m, inner: string) => `(${esc(inner)})`)
    .replace(/\|([^|\n]*)\|/g, (_m, inner: string) => `|${esc(inner)}|`);
}

export async function renderMermaid(code: string): Promise<{ id: string; svg: string }> {
  await ensureFrame();
  counter += 1;
  const id = `mmd-${counter}-${Math.random().toString(36).slice(2, 8)}`;
  const svg = await new Promise<string>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      pending.delete(id);
      reject(new Error("The diagram took too long to render."));
    }, RENDER_TIMEOUT_MS);
    pending.set(id, { resolve, reject, timer });
    frame?.contentWindow?.postMessage({ type: "naadi:mermaid:render", id, code: escapeMermaidLabels(code) }, "*");
  });
  return { id, svg };
}

// ── One shared stylesheet for every rendered diagram ──

let sheet: CSSStyleSheet | null = null;
const rules = new Map<string, string>();

function syncSheet(): void {
  if (typeof CSSStyleSheet === "undefined" || !("adoptedStyleSheets" in document)) return;
  if (!sheet) {
    sheet = new CSSStyleSheet();
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }
  sheet.replaceSync([...rules.values()].join("\n"));
}

export function releaseDiagramStyles(id: string): void {
  if (rules.delete(id)) syncSheet();
}

/** Number of diagrams whose rules are in the shared sheet (tests: nothing leaks after unmount). */
export function diagramStyleCount(): number {
  return rules.size;
}

const ENTITIES: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };

function unescapeXml(s: string): string {
  return s.replace(/&(amp|lt|gt|quot|#39);/g, (m) => ENTITIES[m] ?? m);
}

/**
 * Take every inline style out of the serialised SVG BEFORE anything parses it: WebKit parses DOMPurify's
 * working document under the page's CSP, so a `style` attribute or `<style>` element would already be
 * refused (and reported) there. Attributes become `data-naadi-style` indexes; <style> text is collected.
 */
export function extractStyles(svgText: string): { markup: string; css: string[]; styles: string[] } {
  const css: string[] = [];
  const styles: string[] = [];
  const markup = svgText
    .replace(/<style\b[^>]*>([\s\S]*?)<\/style>/gi, (_m, body: string) => {
      css.push(unescapeXml(body.replace(/^<!\[CDATA\[|\]\]>$/g, "")));
      return "";
    })
    .replace(/\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi, (_m, dq: string | undefined, sq: string | undefined) => {
      styles.push(unescapeXml(dq ?? sq ?? ""));
      return ` data-naadi-style="${styles.length - 1}"`;
    });
  return { markup, css, styles };
}

/**
 * Sanitise `svgText` and move it into `host` without inline styles; `id` keys its rules in the shared sheet.
 * Styles are taken out of the text first (extractStyles), the rest goes through DOMPurify's SVG profile, and
 * the declarations are re-applied through `element.style` (CSSOM, which the CSP allows).
 */
export function adoptSvg(host: HTMLElement, svgText: string, id: string): void {
  const { markup, css, styles } = extractStyles(svgText);
  const clean = DOMPurify(window).sanitize(markup, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["foreignObject", "script", "style", "a", "image", "use"],
    FORBID_ATTR: ["style"],
    ADD_ATTR: ["data-naadi-style"],
    RETURN_DOM_FRAGMENT: true,
  }) as DocumentFragment;
  const svg = clean.querySelector("svg");
  if (!svg) throw new Error("The diagram output is not an SVG image.");
  host.replaceChildren(svg);
  for (const el of Array.from(host.querySelectorAll<SVGElement>("[data-naadi-style]"))) {
    const st = styles[Number(el.getAttribute("data-naadi-style"))] ?? "";
    el.removeAttribute("data-naadi-style");
    for (const decl of st.split(";")) {
      const at = decl.indexOf(":");
      if (at > 0) el.style.setProperty(decl.slice(0, at).trim(), decl.slice(at + 1).trim());
    }
  }
  if (css.some((c) => c.trim())) {
    rules.set(id, css.join("\n"));
    syncSheet();
  }
}
