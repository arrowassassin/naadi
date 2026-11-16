import { useEffect, useState, useSyncExternalStore } from "react";

/**
 * Read CSS custom properties from :root once the stylesheet has applied.
 *
 * Vite emits the module script before the stylesheet link, and WebKit can run the module before a slow
 * stylesheet arrives (seen on the live Space), so the first read may be empty: read again whenever a
 * stylesheet or the whole page finishes loading.
 */
export function useCssVars<T extends string>(names: readonly T[]): Record<T, string> {
  const [values, setValues] = useState<Record<T, string>>(() => read(names));
  const key = names.join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` is the stable identity of `names`
  useEffect(() => {
    const update = () => setValues((prev) => sameValues(prev, read(names)));
    update();
    const links = Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'));
    for (const l of links) l.addEventListener("load", update);
    window.addEventListener("load", update);
    return () => {
      for (const l of links) l.removeEventListener("load", update);
      window.removeEventListener("load", update);
    };
  }, [key]);
  return values;
}

function sameValues<T extends string>(prev: Record<T, string>, next: Record<T, string>): Record<T, string> {
  const keys = Object.keys(next) as T[];
  return keys.length === Object.keys(prev).length && keys.every((k) => prev[k] === next[k]) ? prev : next;
}

function read<T extends string>(names: readonly T[]): Record<T, string> {
  const out = {} as Record<T, string>;
  const style = typeof window === "undefined" ? null : getComputedStyle(document.documentElement);
  for (const n of names) out[n] = style ? style.getPropertyValue(n).trim() : "";
  return out;
}

function subscribeReducedMotion(onChange: () => void): () => void {
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    () => false,
  );
}
