import { getMe, getNav, type MeResponse, type MeUpdate, type NavResponse, updateMe } from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type SdkResult, unwrap } from "./apiError";
import { accessToken, refreshAccessToken, signOut, useAuth } from "./session";
import { readUserCache, writeUserCache } from "./userCache";

type Headers = Record<string, string>;

/**
 * Call a protected endpoint with the current access token. An expired token is refreshed once and the
 * call retried; any other 401 ends the local session so the router sends the user to sign in.
 */
export async function authed<T>(make: (headers: Headers) => Promise<SdkResult<T>>): Promise<T> {
  const token = await accessToken();
  if (!token) throw new ApiError("Sign in to continue.", 401, "unauthorized", null);
  try {
    return await unwrap(make({ authorization: `Bearer ${token}` }));
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) throw err;
    if (err.code === "token_expired") {
      const fresh = await refreshAccessToken();
      if (fresh) {
        try {
          return await unwrap(make({ authorization: `Bearer ${fresh}` }));
        } catch (retryErr) {
          if (retryErr instanceof ApiError && retryErr.status === 401) await signOut("local");
          throw retryErr;
        }
      }
    }
    await signOut("local");
    throw err;
  }
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const meKey = (userId: string | null) => ["me", userId] as const;
export const navKey = (userId: string | null) => ["nav", userId] as const;

export function useMe() {
  const userId = useUserId();
  return useQuery({
    queryKey: meKey(userId),
    enabled: userId !== null,
    queryFn: async ({ signal }) => {
      const me = await authed<MeResponse>((headers) => getMe({ signal, headers }));
      if (userId) writeUserCache("me", userId, me);
      return me;
    },
    initialData: () => (userId ? readUserCache<MeResponse>("me", userId) : undefined),
    initialDataUpdatedAt: 0,
    staleTime: 60_000,
  });
}

export function useNav() {
  const userId = useUserId();
  return useQuery({
    queryKey: navKey(userId),
    enabled: userId !== null,
    queryFn: async ({ signal }) => {
      const nav = await authed<NavResponse>((headers) => getNav({ signal, headers }));
      if (userId) writeUserCache("nav", userId, nav);
      return nav;
    },
    initialData: () => (userId ? readUserCache<NavResponse>("nav", userId) : undefined),
    initialDataUpdatedAt: 0,
    staleTime: 5 * 60_000,
  });
}

export function useUpdateMe() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MeUpdate) => authed<MeResponse>((headers) => updateMe({ body, headers })),
    onSuccess: (me) => {
      qc.setQueryData(meKey(userId), me);
      if (userId) writeUserCache("me", userId, me);
    },
  });
}
