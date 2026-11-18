/**
 * Node-side HTTP for the live-auth run (segment 22, lead decision 20261117T060320). Every request that carries a
 * secret (the HF token, a Supabase session, the Space cookie, the anon key) is made here with undici's fetch,
 * through the sandbox's HTTPS proxy (EnvHttpProxyAgent reads HTTPS_PROXY / NO_PROXY), and never through Playwright:
 * Playwright's request context records headers in its call log, which lands in error-context.md on a failure.
 * Nothing here prints a header or a body; failures report the status only.
 */
import { EnvHttpProxyAgent, fetch } from "undici";

const dispatcher = new EnvHttpProxyAgent();

export interface NodeResponse {
  status: number;
  json<T>(): Promise<T>;
}

export async function nodeFetch(
  url: string,
  init: { method?: string; headers?: Record<string, string>; body?: string } = {},
): Promise<NodeResponse> {
  const res = await fetch(url, { ...init, dispatcher, redirect: "manual" });
  return { status: res.status, json: async <T>() => (await res.json()) as T };
}

/** Private Space: exchange HF_TOKEN for the Space's short-lived JWT (the value `?__sign=` takes). */
export async function spaceSignature(spaceId: string, hfToken: string): Promise<string> {
  const res = await nodeFetch(`https://huggingface.co/api/spaces/${spaceId}/jwt`, {
    headers: { authorization: `Bearer ${hfToken}` },
  });
  if (res.status !== 200) throw new Error(`HF Space JWT exchange answered HTTP ${res.status}`);
  return (await res.json<{ token: string }>()).token;
}

/** A `Cookie` header value from cookies the browser holds for the Space (set by `?__sign=`). */
export function cookieHeader(cookies: { name: string; value: string }[]): string {
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}
