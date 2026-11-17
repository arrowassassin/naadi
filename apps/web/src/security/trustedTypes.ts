/**
 * Trusted Types, step 1 of the rollout (segment 22). The server sends `require-trusted-types-for 'script'` as
 * REPORT-ONLY (naadi_api/security.py), so nothing is blocked yet; every string that reaches a script sink is
 * reported to /api/csp-report.
 *
 * This default policy turns the one kind of sink the app uses on purpose into trusted values: script URLs on our
 * own origin (the service worker, lazily loaded chunks). Any other origin throws, which the browser reports as a
 * violation. HTML sinks get no default handler on purpose: any string reaching innerHTML must stay visible in the
 * reports before enforcement. The app's only HTML parse is inert (`parseInertHtml` below: DOMParser, no scripts
 * run and nothing loads; SafeHtml then rebuilds an allowlist of nodes as React elements), and it has its own
 * module-private policy, `naadi-inert-html`, that no other code can reach.
 *
 * Enforcement (later): `require-trusted-types-for 'script'; trusted-types default naadi-inert-html`.
 */

interface TrustedTypePolicyFactoryLike {
  createPolicy(
    name: string,
    rules: { createScriptURL?: (input: string) => string; createHTML?: (input: string) => string },
  ): unknown;
}

interface InertHtmlPolicy {
  createHTML(input: string): string;
}

export function sameOriginScriptUrl(input: string, origin: string): string {
  const url = new URL(input, origin);
  if (url.origin !== origin) throw new TypeError("Trusted Types: script URL from another origin");
  return url.href;
}

let installed = false;

export function installTrustedTypesPolicy(win: Window = window): boolean {
  const tt = (win as unknown as { trustedTypes?: TrustedTypePolicyFactoryLike }).trustedTypes;
  if (installed) return true;
  if (!tt?.createPolicy) return false;
  try {
    tt.createPolicy("default", { createScriptURL: (input) => sameOriginScriptUrl(input, win.location.origin) });
    installed = true;
  } catch {
    // A default policy already exists (another bundle on the page made it): keep it.
  }
  return installed;
}

let inert: InertHtmlPolicy | null | undefined;

function inertPolicy(): InertHtmlPolicy | null {
  if (inert !== undefined) return inert;
  const tt = (globalThis as unknown as { trustedTypes?: TrustedTypePolicyFactoryLike }).trustedTypes;
  try {
    inert = tt?.createPolicy
      ? (tt.createPolicy("naadi-inert-html", { createHTML: (s) => s }) as InertHtmlPolicy)
      : null;
  } catch {
    inert = null;
  }
  return inert;
}

/**
 * Parse an HTML fragment into an INERT document (DOMParser: scripts never run, resources never load). The result
 * must only be read (text, an allowlist walk), never inserted into the page.
 */
export function parseInertHtml(html: string): Document {
  const p = inertPolicy();
  // The trusted value is a TrustedHTML object at run time; DOMParser's typing only knows strings.
  return new DOMParser().parseFromString((p ? p.createHTML(html) : html) as string, "text/html");
}

// Installed on import: main.tsx imports this module first, so it evaluates before every other module.
if (typeof window !== "undefined") installTrustedTypesPolicy();
