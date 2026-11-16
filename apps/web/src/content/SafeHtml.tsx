import { createElement, Fragment, type ReactNode } from "react";

/**
 * Renders a small HTML fragment (question stems and options) as React elements through an allowlist:
 * only the formatting tags below survive, with no attributes except table spans. Everything else is
 * unwrapped to its text, so markup can never inject scripts, styles, links, images or event handlers.
 * Parsing uses an inert DOMParser document (no scripts run, no resources load).
 */
const ALLOWED = new Set([
  "p",
  "br",
  "b",
  "strong",
  "i",
  "em",
  "u",
  "sub",
  "sup",
  "small",
  "code",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "span",
  "div",
]);
const VOID = new Set(["br"]);
const DROP = new Set(["script", "style", "template", "iframe", "object", "embed", "noscript", "svg", "math"]);
const MAX_NODES = 5000;

function span(el: Element, name: string): number | undefined {
  const n = Number(el.getAttribute(name));
  return Number.isInteger(n) && n > 1 && n <= 50 ? n : undefined;
}

function convert(node: Node, key: string, budget: { left: number }): ReactNode {
  if (budget.left-- <= 0) return null;
  if (node.nodeType === 3) return node.textContent;
  if (node.nodeType !== 1) return null;
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (DROP.has(tag)) return null;
  const children = Array.from(el.childNodes).map((c, i) => convert(c, `${key}.${i}`, budget));
  if (!ALLOWED.has(tag)) return createElement(Fragment, { key }, ...children);
  if (VOID.has(tag)) return createElement(tag, { key });
  const props: Record<string, unknown> = { key };
  if (tag === "td" || tag === "th") {
    props.colSpan = span(el, "colspan");
    props.rowSpan = span(el, "rowspan");
  }
  return createElement(tag, props, ...children);
}

export function sanitizeToReact(html: string): ReactNode {
  if (typeof DOMParser === "undefined") return html.replace(/<[^>]*>/g, "");
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const budget = { left: MAX_NODES };
  return Array.from(doc.body.childNodes).map((c, i) => convert(c, String(i), budget));
}

export function SafeHtml({ html, className }: { html: string; className?: string }) {
  return <div className={className}>{sanitizeToReact(html)}</div>;
}
