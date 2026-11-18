import { type MeResponse, updateMe } from "@naadi/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { authed, meKey, useMe } from "../../auth/api";
import { useAuth } from "../../auth/session";
import { writeUserCache } from "../../auth/userCache";

/**
 * Handwriting search is opt-in: on only when `profiles.settings.handwriting_search` is exactly `true` (absent, null
 * or false = off, matching the API's ink/ocr/queue.py). No ink image is sent before the student turns it on.
 */
export function handwritingSearchOn(settings: unknown): boolean {
  return (
    !!settings && typeof settings === "object" && (settings as Record<string, unknown>).handwriting_search === true
  );
}

/** The cached /api/me with only the flag changed (`undefined` removes the key), leaving every other setting as is. */
function withFlag(me: MeResponse, value: unknown): MeResponse {
  const settings = { ...(me.profile.settings ?? {}) };
  if (value === undefined) delete settings.handwriting_search;
  else settings.handwriting_search = value;
  return { ...me, profile: { ...me.profile, settings } };
}

interface Rollback {
  had: boolean;
  before: unknown;
}

/**
 * Settings → Pencil & ink → Handwriting search. Sends only `{settings: {handwriting_search}}` (PATCH /api/me merges
 * settings one level deep, so the ink saver's `settings.ink` and this flag never overwrite each other). The switch
 * moves at once (optimistic); on failure only the flag is put back and the error is kept for the page to show.
 * `cleared` is true after the server confirmed turning it off (it deleted the recognised text by then).
 */
export function useHandwritingSearch() {
  const me = useMe();
  const auth = useAuth();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const qc = useQueryClient();
  const [cleared, setCleared] = useState(false);

  const mutation = useMutation<MeResponse, Error, boolean, Rollback>({
    mutationFn: (on) =>
      authed<MeResponse>((headers) => updateMe({ body: { settings: { handwriting_search: on } }, headers })),
    onMutate: async (on) => {
      setCleared(false);
      const key = meKey(userId);
      // A /api/me refetch in flight must not land over the optimistic value.
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<MeResponse>(key);
      const before = (prev?.profile.settings as Record<string, unknown> | undefined)?.handwriting_search;
      if (prev) qc.setQueryData(key, withFlag(prev, on));
      return { had: prev !== undefined, before };
    },
    onError: (_err, _on, ctx) => {
      const key = meKey(userId);
      const cur = qc.getQueryData<MeResponse>(key);
      // Only the flag is rolled back: another setting saved meanwhile (the ink saver) stays.
      if (cur && ctx?.had) qc.setQueryData(key, withFlag(cur, ctx.before));
    },
    onSuccess: (fresh, on) => {
      qc.setQueryData(meKey(userId), fresh);
      if (userId) writeUserCache("me", userId, fresh);
      if (!on) setCleared(true);
    },
  });

  const mutate = mutation.mutate;
  const set = useCallback((on: boolean) => mutate(on), [mutate]);

  return {
    on: handwritingSearchOn(me.data?.profile.settings),
    loaded: me.data !== undefined,
    set,
    saving: mutation.isPending,
    error: mutation.error,
    cleared,
  };
}
