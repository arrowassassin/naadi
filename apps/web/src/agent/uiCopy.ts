import { getPublicConfig, UI_COPY_DEFAULTS, uiText } from "@naadi/shared";
import { useQuery } from "@tanstack/react-query";

/**
 * Editable UI strings from `app_config.ui_copy`, served with GET /api/public-config (segment 05; cached 60 s
 * by the server). `copy(key, vars)` fills `{placeholders}`; while loading, or for a key the config lacks, it
 * uses the shipped default; it returns null only for a key with neither (the caller shows the bare value).
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
    // Configured text, else the shipped default (uiText); null only for a key that is neither, so the
    // caller can show the bare value instead of a key name.
    if (!q.data?.[key] && !UI_COPY_DEFAULTS[key]) return null;
    return uiText(q.data ?? null, key, vars);
  };
}
