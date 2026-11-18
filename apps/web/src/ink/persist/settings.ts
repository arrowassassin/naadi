import type { InkToolName } from "@naadi/ink";
import { type InkColor, InkColorSchema } from "@naadi/shared";
import { useCallback, useEffect, useRef } from "react";
import { useMe, useUpdateMe } from "../../auth/api";

/** `profiles.settings.ink` (validated by the API's InkSettings model). */
export interface InkSettings {
  pen_only: boolean;
  predict: boolean;
  tool?: InkToolName;
  color?: InkColor;
}

export const INK_SETTINGS_DEFAULTS: InkSettings = { pen_only: true, predict: true };

const TOOLS: readonly InkToolName[] = ["pen", "marker", "eraser", "lasso", "shapes", "text"];

/** Read whatever is stored, keeping only valid fields (an older or hand-edited value never breaks the canvas). */
export function parseInkSettings(raw: unknown): InkSettings {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const out: InkSettings = {
    pen_only: typeof o.pen_only === "boolean" ? o.pen_only : INK_SETTINGS_DEFAULTS.pen_only,
    predict: typeof o.predict === "boolean" ? o.predict : INK_SETTINGS_DEFAULTS.predict,
  };
  if (TOOLS.includes(o.tool as InkToolName)) out.tool = o.tool as InkToolName;
  const color = InkColorSchema.safeParse(o.color);
  if (color.success) out.color = color.data;
  return out;
}

/**
 * The student's Pencil & ink settings plus a debounced saver for the last tool and colour (the whole `ink` object is
 * sent: the API merges settings one level deep). Works offline from the cached /api/me.
 */
export function useInkSettings() {
  const me = useMe();
  const update = useUpdateMe();
  const settings = parseInkSettings((me.data?.profile.settings as Record<string, unknown> | null)?.ink);
  const latest = useRef(settings);
  latest.current = settings;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<Partial<InkSettings>>({});
  const mutate = update.mutate;

  const save = useCallback(
    (patch: Partial<InkSettings>, opts: { debounceMs?: number } = {}) => {
      pending.current = { ...pending.current, ...patch };
      if (timer.current) clearTimeout(timer.current);
      const send = () => {
        timer.current = null;
        const ink = { ...latest.current, ...pending.current };
        pending.current = {};
        mutate({ settings: { ink } });
      };
      if (opts.debounceMs) timer.current = setTimeout(send, opts.debounceMs);
      else send();
    },
    [mutate],
  );

  // Leaving the page sends a debounced change now rather than dropping it.
  useEffect(
    () => () => {
      if (!timer.current) return;
      clearTimeout(timer.current);
      timer.current = null;
      mutate({ settings: { ink: { ...latest.current, ...pending.current } } });
    },
    [mutate],
  );

  return { settings, loaded: me.data !== undefined, save, saving: update.isPending, error: update.error };
}
