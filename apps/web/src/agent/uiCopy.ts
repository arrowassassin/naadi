import { useQuery } from "@tanstack/react-query";

/**
 * Editable UI strings from `app_config.ui_copy`, served by GET /api/config/public (segment 05, cached 60 s
 * server-side). `copy(key, vars)` fills `{placeholders}`; it returns null while the strings are loading or
 * when the key is not configured, and the caller then shows the bare value (for example only the time).
 */
type UiCopy = Record<string, string>;

async function fetchUiCopy(signal?: AbortSignal): Promise<UiCopy> {
  const res = await fetch("/api/config/public", { signal, headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body: unknown = await res.json();
  const raw = typeof body === "object" && body !== null ? (body as { ui_copy?: unknown }).ui_copy : undefined;
  const out: UiCopy = {};
  if (typeof raw === "object" && raw !== null) {
    for (const [k, v] of Object.entries(raw)) if (typeof v === "string") out[k] = v;
  }
  return out;
}

export function useUiCopy() {
  const q = useQuery({
    queryKey: ["config", "public", "ui_copy"],
    queryFn: ({ signal }) => fetchUiCopy(signal),
    staleTime: 60_000,
    retry: 1,
  });
  return (key: string, vars: Record<string, string> = {}): string | null => {
    const template = q.data?.[key];
    if (!template) return null;
    return template.replace(/\{(\w+)\}/g, (m, name: string) => vars[name] ?? m);
  };
}
