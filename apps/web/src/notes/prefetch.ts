/**
 * Fetch chunks into the browser cache once the page has painted and is idle, so they are there if the student goes
 * offline before opening the feature. Failures are ignored (this is only a head start).
 */
export function prefetchChunks(...loaders: (() => Promise<unknown>)[]): () => void {
  const w = window as Window & {
    requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  const run = () => {
    for (const load of loaders) load().catch(() => undefined);
  };
  if (w.requestIdleCallback) {
    const id = w.requestIdleCallback(run, { timeout: 2500 });
    return () => w.cancelIdleCallback?.(id);
  }
  const id = window.setTimeout(run, 800);
  return () => window.clearTimeout(id);
}
