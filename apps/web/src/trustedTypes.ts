/**
 * Trusted Types (report-only today, see security.py): a string handed to a script sink is reported. The one sink the
 * app uses is the service worker URL, so it goes through a named policy that only vouches for that exact path.
 */
interface TrustedPolicy {
  createScriptURL(input: string): string;
}
interface TrustedTypesApi {
  createPolicy(name: string, rules: { createScriptURL: (input: string) => string }): TrustedPolicy;
}

export const SW_PATH = "/sw.js";
let policy: TrustedPolicy | null = null;

/** `/sw.js` as a TrustedScriptURL where the browser has Trusted Types, the plain string where it does not. */
export function serviceWorkerScriptUrl(): string {
  const api = (globalThis as { trustedTypes?: TrustedTypesApi }).trustedTypes;
  if (!api) return SW_PATH;
  policy ??= api.createPolicy("naadi-service-worker", {
    createScriptURL: (input) => {
      if (input !== SW_PATH) throw new TypeError("not the service worker script");
      return input;
    },
  });
  return policy.createScriptURL(SW_PATH);
}

/** Test hook: forget the cached policy. */
export function resetTrustedPolicy(): void {
  policy = null;
}
