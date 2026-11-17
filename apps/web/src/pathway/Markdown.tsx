import { type ReactNode, useMemo } from "react";
import { safeHttpsUrl } from "./format";

/**
 * A small Markdown renderer for the seeded pathway text (task detail, stage summaries and guides). It builds React
 * elements, never HTML, so nothing in the source can inject markup. Links open in a new tab and only `https:` links
 * become links; everything else stays plain text. Supported: headings, paragraphs, bullet and numbered lists,
 * tables, quotes, rules, **bold**, *italic*, `code` and [links](https://…).
 */

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

export function inline(text: string, keyBase = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const tok = m[0];
    const key = `${keyBase}-${n++}`;
    if (tok.startsWith("**")) out.push(<strong key={key}>{inline(tok.slice(2, -2), key)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[")) {
      const close = tok.indexOf("](");
      const label = tok.slice(1, close);
      const href = safeHttpsUrl(tok.slice(close + 2, -1));
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {label}
          </a>
        ) : (
          label
        ),
      );
    } else out.push(<em key={key}>{inline(tok.slice(1, -1), key)}</em>);
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const ROW = /^\s*\|.*\|\s*$/;
const SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.trim());
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const blocks = useMemo(() => {
    // Stable keys from the content itself (a repeated text gets a counter), never from a position.
    const seen = new Map<string, number>();
    const uid = (text: string): string => {
      const n = (seen.get(text) ?? 0) + 1;
      seen.set(text, n);
      return `${text}#${n}`;
    };
    const lines = source.replace(/\r\n?/g, "\n").split("\n");
    const out: ReactNode[] = [];
    let i = 0;
    let k = 0;
    while (i < lines.length) {
      const line = lines[i] ?? "";
      const key = `b${k++}`;
      if (!line.trim()) {
        i++;
        continue;
      }
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) {
        const level = Math.min((h[1] ?? "#").length + 1, 5); // the page owns h1; a guide's # becomes h2
        const Tag = `h${level}` as "h2";
        out.push(<Tag key={key}>{inline(h[2] ?? "", key)}</Tag>);
        i++;
      } else if (/^\s*---+\s*$/.test(line)) {
        out.push(<hr key={key} />);
        i++;
      } else if (ROW.test(line) && SEP.test(lines[i + 1] ?? "")) {
        const head = cells(line);
        i += 2;
        const body: string[][] = [];
        while (i < lines.length && ROW.test(lines[i] ?? "")) body.push(cells(lines[i++] ?? ""));
        out.push(
          <div
            key={key}
            className="overflow-x-auto" /* biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable */
            tabIndex={0}
          >
            <table>
              <thead>
                <tr>
                  {head.map((c) => (
                    <th key={uid(`${key}-h-${c}`)} scope="col">
                      {inline(c, `${key}-h`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((r) => (
                  <tr key={uid(`${key}-r-${r.join("|")}`)}>
                    {r.map((c) => (
                      <td key={uid(`${key}-c-${c}`)}>{inline(c, `${key}-c`)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
      } else if (/^\s*([-*•])\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
        const ordered = /^\s*\d+[.)]\s+/.test(line);
        const items: string[] = [];
        const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*•]\s+(.*)$/;
        while (i < lines.length && re.test(lines[i] ?? "")) items.push(re.exec(lines[i++] ?? "")?.[1] ?? "");
        const L = ordered ? "ol" : "ul";
        out.push(
          <L key={key}>
            {items.map((it) => (
              <li key={uid(`${key}-li-${it}`)}>{inline(it, `${key}-li`)}</li>
            ))}
          </L>,
        );
      } else if (/^\s*>/.test(line)) {
        const q: string[] = [];
        while (i < lines.length && /^\s*>/.test(lines[i] ?? "")) q.push((lines[i++] ?? "").replace(/^\s*>\s?/, ""));
        out.push(<blockquote key={key}>{inline(q.join(" "), key)}</blockquote>);
      } else {
        const p: string[] = [];
        while (
          i < lines.length &&
          (lines[i] ?? "").trim() &&
          !/^(#{1,4}\s|\s*---+\s*$|\s*>|\s*[-*•]\s|\s*\d+[.)]\s)/.test(lines[i] ?? "") &&
          !ROW.test(lines[i] ?? "")
        )
          p.push((lines[i++] ?? "").trim());
        if (p.length === 0) {
          p.push((lines[i++] ?? "").trim());
        }
        out.push(<p key={key}>{inline(p.join(" "), key)}</p>);
      }
    }
    return out;
  }, [source]);
  return <div className={className ? `pathway-md ${className}` : "pathway-md"}>{blocks}</div>;
}
