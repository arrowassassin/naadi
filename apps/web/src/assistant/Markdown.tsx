import { cx } from "@naadi/ui";
import { type ComponentProps, memo, type ReactNode, useEffect, useMemo, useState } from "react";
import type { CustomRendererProps, PluginConfig, StreamdownProps } from "streamdown";
import { useAuth } from "../auth/session";
import { MermaidBlock } from "../content/RichNodes";
import type { ContentEnv } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { allowedUrl, imagePrefixes } from "./mediaHosts";
import type { Citation } from "./protocol";

/*
 * Model text (GFM, KaTeX `$…$`, fenced code with Shiki, fenced `mermaid`) rendered with Streamdown.
 * - No raw HTML: the model's HTML is skipped (no rehype-raw), the sanitize step stays.
 * - Hardening: links https or same-origin only; images only from the hosts the CSP allows; data: images never.
 * - KaTeX renders MathML only (no inline styles; MathML Core is native in Safari and Chrome), with the app's
 *   katex (one copy, pnpm override).
 * - Mermaid uses the app's sandboxed renderer (content/mermaidHost.ts, mermaid 12), not @streamdown/mermaid.
 * - [n] markers become citation chips linked to the citation with that n; unknown n stays text.
 * - Streamdown and its plugins (unified, micromark, Shiki, KaTeX) load on demand, outside the surface's chunk
 *   (performance budget): `preloadMarkdown()` starts the load when the surface mounts; until it lands, text
 *   shows as plain wrapped text.
 */

type Pluggable = NonNullable<StreamdownProps["rehypePlugins"]>[number];
type HastNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

interface MarkdownLib {
  Streamdown: typeof import("streamdown").Streamdown;
  math: PluginConfig["math"];
  code: PluginConfig["code"];
  harden: Pluggable;
  hardenDefaults: Record<string, unknown>;
  sanitize: Pluggable;
}

let lib: MarkdownLib | null = null;
let loading: Promise<MarkdownLib> | null = null;

/** Loads Streamdown and its math/code plugins once (separate chunks); safe to call repeatedly. */
export function preloadMarkdown(): Promise<MarkdownLib> {
  loading ??= Promise.all([import("streamdown"), import("@streamdown/math"), import("@streamdown/code")]).then(
    ([sd, { createMathPlugin }, { createCodePlugin }]) => {
      const mathBase = createMathPlugin({ singleDollarTextMath: true });
      const [rehypeKatex, katexOptions] = mathBase.rehypePlugin as [Pluggable, Record<string, unknown>];
      const [harden, hardenDefaults] = sd.defaultRehypePlugins.harden as [Pluggable, Record<string, unknown>];
      lib = {
        Streamdown: sd.Streamdown,
        math: {
          ...mathBase,
          rehypePlugin: [
            rehypeKatex,
            {
              ...katexOptions,
              output: "mathml",
              throwOnError: false,
              trust: false,
              strict: "ignore",
              maxSize: 50,
              maxExpand: 500,
            },
          ] as unknown as Pluggable,
          getStyles: undefined,
        },
        code: createCodePlugin({ themes: ["github-dark", "github-dark"] }),
        harden,
        hardenDefaults,
        sanitize: sd.defaultRehypePlugins.sanitize as Pluggable,
      };
      return lib;
    },
  );
  loading.catch(() => {
    loading = null; // a failed chunk load (offline) is retried on the next render that needs it
  });
  return loading;
}

function useMarkdownLib(): MarkdownLib | null {
  const [ready, setReady] = useState<MarkdownLib | null>(lib);
  useEffect(() => {
    if (ready) return;
    let alive = true;
    preloadMarkdown().then(
      (l) => alive && setReady(l),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [ready]);
  return ready;
}

const SKIP = new Set(["code", "pre", "a", "math", "annotation"]);

/**
 * rehype: `[n]` in text → <cite data-n> when a citation with that n exists. Options are JSON (Streamdown caches
 * its processors by plugin name + JSON options, so a closure would be reused with stale data).
 */
export function rehypeCiteMarkers(options: { known?: number[] } = {}) {
  const known = new Set(options.known ?? []);
  return (tree: HastNode) => {
    const walk = (node: HastNode) => {
      if (!node.children || (node.tagName && SKIP.has(node.tagName))) return;
      const next: HastNode[] = [];
      for (const child of node.children) {
        if (child.type !== "text" || !child.value || !child.value.includes("[")) {
          walk(child);
          next.push(child);
          continue;
        }
        let last = 0;
        for (const m of child.value.matchAll(/\[(\d{1,3})\]/g)) {
          const n = Number(m[1]);
          if (!known.has(n)) continue;
          const at = m.index ?? 0;
          if (at > last) next.push({ type: "text", value: child.value.slice(last, at) });
          next.push({ type: "element", tagName: "cite", properties: { dataN: String(n) }, children: [] });
          last = at + m[0].length;
        }
        if (last === 0) next.push(child);
        else if (last < child.value.length) next.push({ type: "text", value: child.value.slice(last) });
      }
      node.children = next;
    };
    walk(tree);
  };
}

function MermaidFence({ code, isIncomplete }: CustomRendererProps) {
  const t = useUiText();
  const env = useMemo<ContentEnv>(
    () => ({
      media: {},
      questions: {},
      mode: "prod",
      sectionId: "assistant",
      headingBase: 3,
      minHeading: 1,
      embedOrigin: window.location.origin,
      text: (k, v) => t(k, v),
    }),
    [t],
  );
  if (isIncomplete) {
    return (
      <pre className="m-0 overflow-x-auto rounded-inner bg-surface-2 p-3 font-mono text-label-sm text-text-2">
        {code}
      </pre>
    );
  }
  return <MermaidBlock code={code} env={env} />;
}

function CitationChip({ n, citation }: { n: number; citation: Citation | undefined }) {
  const t = useUiText();
  const label = t("agent.citation_label", { n: String(n), title: citation?.title ?? "" });
  const cls =
    "mx-0.5 inline-flex min-h-6 min-w-6 items-center justify-center rounded-pill border border-violet-line bg-violet-ground px-1.5 align-baseline font-mono text-[0.6875rem] text-violet-text no-underline hover:text-violet-text";
  if (citation?.url && allowedUrl(citation.url, ["https://"], window.location.origin)) {
    return (
      <a href={citation.url} target="_blank" rel="noopener noreferrer" aria-label={label} className={cls}>
        {n}
      </a>
    );
  }
  return (
    <a href={`#cite-${citation?.id ?? n}`} aria-label={label} className={cls}>
      {n}
    </a>
  );
}

type AProps = ComponentProps<"a"> & { node?: unknown };

export interface MarkdownProps {
  text: string;
  streaming: boolean;
  citations?: readonly Citation[];
  className?: string;
}

function MarkdownImpl(props: MarkdownProps) {
  const md = useMarkdownLib();
  if (!md) {
    return (
      <div className={cx("naadi-md flex flex-col gap-3 text-reading text-text", props.className)}>
        <p className="m-0 whitespace-pre-wrap">{props.text}</p>
      </div>
    );
  }
  return <MarkdownLoaded {...props} md={md} />;
}

function MarkdownLoaded({ text, streaming, citations = [], className, md }: MarkdownProps & { md: MarkdownLib }) {
  const { Streamdown, harden, hardenDefaults, sanitize } = md;
  const t = useUiText();
  const auth = useAuth();
  const supabaseUrl = auth.status === "signed-in" || auth.status === "signed-out" ? auth.config.supabase_url : null;
  const origin = window.location.origin;
  const images = useMemo(() => imagePrefixes(origin, supabaseUrl), [origin, supabaseUrl]);
  const byN = useMemo(() => new Map(citations.filter((c) => c.n).map((c) => [c.n as number, c])), [citations]);
  const knownKey = [...byN.keys()].sort((a, b) => a - b).join(",");

  const rehypePlugins = useMemo(
    () =>
      [
        sanitize,
        [
          harden,
          {
            ...hardenDefaults,
            defaultOrigin: origin,
            // harden matches prefixes by origin: any http(s) link passes here and the `a` component below
            // keeps only https and same-origin ones (everything else renders as plain text).
            allowedLinkPrefixes: ["*"],
            allowedImagePrefixes: images,
            allowedProtocols: [],
            allowDataImages: false,
            linkBlockPolicy: "text-only",
            imageBlockPolicy: "remove",
          },
        ],
        [rehypeCiteMarkers, { known: knownKey ? knownKey.split(",").map(Number) : [] }],
      ] as Pluggable[],
    [origin, images, knownKey, harden, hardenDefaults, sanitize],
  );

  const components = useMemo(
    () => ({
      p: ({ children }: { children?: ReactNode }) => <p className="m-0">{children}</p>,
      a: ({ href, children }: AProps) => {
        if (!href || !allowedUrl(href, ["https://", `${origin}/`], origin)) return <span>{children}</span>;
        const external = !href.startsWith(`${origin}/`) && !href.startsWith("/");
        return (
          <a
            href={href}
            target={external ? "_blank" : undefined}
            rel={external ? "noopener noreferrer" : undefined}
            className="text-violet-text underline decoration-violet-line underline-offset-2 hover:text-text"
          >
            {children}
          </a>
        );
      },
      img: ({ src, alt }: ComponentProps<"img">) =>
        typeof src === "string" && allowedUrl(src, images, origin) ? (
          <img
            src={src}
            alt={alt ?? ""}
            loading="lazy"
            decoding="async"
            className="h-auto max-w-full rounded-inner bg-paper"
          />
        ) : null,
      ul: ({ children }: { children?: ReactNode }) => (
        <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5">{children}</ul>
      ),
      ol: ({ children }: { children?: ReactNode }) => (
        <ol className="m-0 flex list-decimal flex-col gap-1.5 pl-5">{children}</ol>
      ),
      li: ({ children }: { children?: ReactNode }) => <li className="pl-0.5">{children}</li>,
      h1: ({ children }: { children?: ReactNode }) => (
        <h3 className="m-0 font-bold font-display text-[1.25rem]">{children}</h3>
      ),
      h2: ({ children }: { children?: ReactNode }) => (
        <h3 className="m-0 font-bold font-display text-[1.125rem]">{children}</h3>
      ),
      h3: ({ children }: { children?: ReactNode }) => (
        <h4 className="m-0 font-bold font-display text-[1.0625rem]">{children}</h4>
      ),
      h4: ({ children }: { children?: ReactNode }) => <h4 className="m-0 font-semibold">{children}</h4>,
      h5: ({ children }: { children?: ReactNode }) => <h4 className="m-0 font-semibold">{children}</h4>,
      h6: ({ children }: { children?: ReactNode }) => <h4 className="m-0 font-semibold">{children}</h4>,
      blockquote: ({ children }: { children?: ReactNode }) => (
        <blockquote className="m-0 border-violet-line border-l-2 pl-3 text-text-2">{children}</blockquote>
      ),
      hr: () => <hr className="m-0 border-line border-t" />,
      inlineCode: ({ children }: { children?: ReactNode }) => (
        <code className="rounded-[6px] bg-surface-2 px-1 py-0.5 font-mono text-[0.875em]">{children}</code>
      ),
      table: ({ children }: { children?: ReactNode }) => (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
        <div tabIndex={0} className="overflow-x-auto rounded-inner border border-line">
          <table className="w-full border-collapse text-left text-ui-sm">{children}</table>
        </div>
      ),
      th: ({ children }: { children?: ReactNode }) => (
        <th
          scope="col"
          className="border-line border-b bg-surface-2 px-3 py-2 font-mono font-medium text-label-sm text-text-2 uppercase"
        >
          {children}
        </th>
      ),
      td: ({ children }: { children?: ReactNode }) => (
        <td className="border-line-soft border-t px-3 py-2 text-text-2">{children}</td>
      ),
      cite: (props: ComponentProps<"cite">) => {
        const p = props as Record<string, unknown>;
        const n = Number(p["data-n"] ?? p.dataN);
        return <CitationChip n={n} citation={byN.get(n)} />;
      },
    }),
    [origin, images, byN],
  );

  const plugins = useMemo<PluginConfig>(
    () => ({ code: md.code, math: md.math, renderers: [{ language: "mermaid", component: MermaidFence }] }),
    [md],
  );

  return (
    <Streamdown
      mode={streaming ? "streaming" : "static"}
      isAnimating={streaming}
      parseIncompleteMarkdown
      skipHtml
      rehypePlugins={rehypePlugins}
      plugins={plugins}
      components={components}
      allowedTags={{ cite: ["data-n", "dataN"] }}
      controls={{ code: { copy: true, download: false }, table: false, mermaid: false, image: false }}
      linkSafety={{ enabled: false }}
      lineNumbers={false}
      translations={{
        copyCode: t("agent.md.copy_code"),
        copied: t("agent.md.copied"),
        imageNotAvailable: t("agent.md.image_unavailable"),
      }}
      className={cx("naadi-md flex flex-col gap-3 text-reading text-text [&_strong]:font-semibold", className)}
    >
      {text}
    </Streamdown>
  );
}

export const Markdown = memo(MarkdownImpl);
