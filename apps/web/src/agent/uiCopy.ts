import { getPublicConfig } from "@naadi/shared";
import { useQuery } from "@tanstack/react-query";

/**
 * Editable UI strings from `app_config.ui_copy`, served with GET /api/public-config (segment 05; cached 60 s
 * by the server). `copy(key, vars)` fills `{placeholders}`; it returns null while the strings are loading or
 * when the key is not configured, and the caller then shows the bare value (for example only the time).
 */
type UiCopy = Record<string, string>;

async function fetchUiCopy(signal?: AbortSignal): Promise<UiCopy> {
  const res = await getPublicConfig({ signal });
  if (res.error !== undefined || !res.data) throw new Error(`HTTP ${res.response?.status ?? 0}`);
  return res.data.ui_copy;
}

export function useUiCopy() {
  const q = useQuery({
    queryKey: ["public-config", "ui_copy"],
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
