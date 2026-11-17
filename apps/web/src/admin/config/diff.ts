/** Line diff of two JSON values (pretty-printed), shown before a config save and for history entries. */

export interface DiffLine {
  op: "equal" | "insert" | "delete";
  text: string;
}

export function pretty(v: unknown): string {
  return v === undefined ? "" : JSON.stringify(v, null, 2);
}

/**
 * LCS line diff. The common prefix and suffix are trimmed first, so the quadratic table covers only the changed
 * middle (a config edit touches a few lines of a long value).
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before === "" ? [] : before.split("\n");
  const b = after === "" ? [] : after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;
  const out: DiffLine[] = a.slice(0, start).map((text) => ({ op: "equal", text }));
  if (n * m > 4_000_000) {
    // Too large to align: show the middle as removed then added.
    for (const text of midA) out.push({ op: "delete", text });
    for (const text of midB) out.push({ op: "insert", text });
  } else {
    const w = m + 1;
    const lcs = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * w + j] =
          midA[i] === midB[j]
            ? (lcs[(i + 1) * w + j + 1] ?? 0) + 1
            : Math.max(lcs[(i + 1) * w + j] ?? 0, lcs[i * w + j + 1] ?? 0);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (midA[i] === midB[j]) {
        out.push({ op: "equal", text: midA[i] ?? "" });
        i++;
        j++;
      } else if ((lcs[(i + 1) * w + j] ?? 0) >= (lcs[i * w + j + 1] ?? 0)) {
        out.push({ op: "delete", text: midA[i] ?? "" });
        i++;
      } else {
        out.push({ op: "insert", text: midB[j] ?? "" });
        j++;
      }
    }
    while (i < n) out.push({ op: "delete", text: midA[i++] ?? "" });
    while (j < m) out.push({ op: "insert", text: midB[j++] ?? "" });
  }
  for (const text of a.slice(endA)) out.push({ op: "equal", text });
  return out;
}

export function jsonDiff(before: unknown, after: unknown): DiffLine[] {
  return lineDiff(pretty(before), pretty(after));
}

export function changedCount(lines: DiffLine[]): number {
  return lines.filter((l) => l.op !== "equal").length;
}

/** Collapse long unchanged runs to `context` lines around changes (null = an elided run of n lines). */
export function withContext(lines: DiffLine[], context = 3): (DiffLine | { op: "skip"; n: number })[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((l, i) => {
    if (l.op === "equal") return;
    for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep[k] = true;
  });
  const out: (DiffLine | { op: "skip"; n: number })[] = [];
  let skipped = 0;
  lines.forEach((l, i) => {
    if (keep[i]) {
      if (skipped) out.push({ op: "skip", n: skipped });
      skipped = 0;
      out.push(l);
    } else skipped++;
  });
  if (skipped) out.push({ op: "skip", n: skipped });
  return out;
}
