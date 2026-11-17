import { parseInertHtml } from "../security/trustedTypes";

/**
 * Text-level pass before any parsing: WebKit checks even an inert (DOMParser) document's `<style>` elements and
 * `style` attributes against the page's CSP and reports each as a violation, so they are removed from the markup
 * itself first. The DOM pass in `stripPastedHtml` then catches whatever a pattern cannot.
 */
function preStrip(html: string): string {
  return html
    .replace(/<style\b[\s\S]*?<\/style\s*>/gi, "")
    .replace(/<link\b[^>]*>/gi, "")
    .replace(/<([a-zA-Z][^\s>/]*)([^>]*)>/g, (_m, name: string, attrs: string) => {
      const kept = attrs.replace(/\s(?:style|class)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "");
      return `<${name}${kept}>`;
    });
}

/**
 * Pasted HTML made safe for the production CSP (no 'unsafe-inline' styles): ProseMirror parses a paste by setting
 * `innerHTML` on a detached element, and every `style="…"` attribute in it is an inline-style violation (a console
 * error and a CSP report) however ordinary the copied page was. The paste is parsed here in an inert document (no
 * styles apply, no script runs), stripped of style, class, `on*` handlers and `<style>`/`<link>`/`<script>`/`<meta>`
 * elements, and only then handed back as text. The editor's schema still decides what survives (it keeps paragraphs,
 * headings, lists, quotes, bold, italic, highlights and the student's own images).
 */
export function stripPastedHtml(html: string): string {
  const doc = parseInertHtml(preStrip(html));
  for (const el of doc.querySelectorAll("style, link, script, meta, base, noscript")) el.remove();
  for (const el of doc.body.querySelectorAll("*")) {
    for (const name of el.getAttributeNames()) {
      if (name === "style" || name === "class" || name.startsWith("on")) el.removeAttribute(name);
    }
  }
  return doc.body.innerHTML;
}
