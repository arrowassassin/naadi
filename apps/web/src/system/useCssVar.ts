import { useEffect, useState, useSyncExternalStore } from "react";

/** Read CSS custom properties from :root once the stylesheet has applied. */
export function useCssVars<T extends string>(names: readonly T[]): Record<T, string> {
  const [values, setValues] = useState<Record<T, string>>(() => read(names));
  const key = names.join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` is the stable identity of `names`
  useEffect(() => {
    setValues(read(names));
  }, [key]);
  return values;
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
