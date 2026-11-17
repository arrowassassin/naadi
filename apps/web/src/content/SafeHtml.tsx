import { createElement, Fragment, type ReactNode } from "react";
import { parseInertHtml } from "../security/trustedTypes";

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

/** Segment 13: renders each text node given its UTF-16 offset in the fragment's text (highlights, anchors). */
export type HtmlTextDecorator = (text: string, offset: number, key: string) => ReactNode;

interface Walk {
  left: number;
  offset: number;
  decorate?: HtmlTextDecorator;
}

function convert(node: Node, key: string, budget: Walk): ReactNode {
  if (budget.left-- <= 0) return null;
  if (node.nodeType === 3) {
    const text = node.textContent ?? "";
    const at = budget.offset;
    budget.offset += text.length;
    return budget.decorate && text ? budget.decorate(text, at, key) : text;
  }
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

interface InertHtmlPolicy {
  createHTML: (input: string) => unknown;
}
let inertPolicy: InertHtmlPolicy | null | undefined;

/**
 * The Trusted Types policy for this one sink (the CSP requires Trusted Types for script sinks). It is safe to pass
 * the string through unchanged: DOMParser builds an inert document (no scripts run, nothing loads), and only the
 * allowlisted tags of that document ever reach React. null where Trusted Types do not exist.
 */
function policy(): InertHtmlPolicy | null {
  if (inertPolicy !== undefined) return inertPolicy;
  const tt = (globalThis as { trustedTypes?: { createPolicy: (n: string, r: InertHtmlPolicy) => InertHtmlPolicy } })
    .trustedTypes;
  try {
    inertPolicy = tt ? tt.createPolicy("naadi-inert-html", { createHTML: (input: string) => input }) : null;
  } catch {
    inertPolicy = null;
  }
  return inertPolicy;
}

/** Parse an HTML fragment into an inert document through the Trusted Types policy. */
export function parseInertHtml(html: string): Document {
  const p = policy();
  const markup = p ? (p.createHTML(html) as string) : html;
  return new DOMParser().parseFromString(markup, "text/html");
}

export function sanitizeToReact(html: string, decorate?: HtmlTextDecorator): ReactNode {
  if (typeof DOMParser === "undefined") return html.replace(/<[^>]*>/g, "");
  const doc = parseInertHtml(`<body>${html}</body>`);
  const budget: Walk = { left: MAX_NODES, offset: 0, decorate };
  return Array.from(doc.body.childNodes).map((c, i) => convert(c, String(i), budget));
}

/** The text nodes the renderer shows, with their offsets (the coordinates `decorate` receives). */
export function htmlTextRuns(html: string): { pos: number; text: string }[] {
  const runs: { pos: number; text: string }[] = [];
  sanitizeToReact(html, (text, offset) => {
    runs.push({ pos: offset, text });
    return text;
  });
  return runs;
}

export function SafeHtml({
  html,
  className,
  decorateText,
}: {
  html: string;
  className?: string;
  decorateText?: HtmlTextDecorator;
}) {
  return <div className={className}>{sanitizeToReact(html, decorateText)}</div>;
}
