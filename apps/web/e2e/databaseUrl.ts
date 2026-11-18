/**
 * Which database and Supabase project the local e2e servers may use. Evaluated when playwright.config.ts
 * loads, before any webServer starts (Playwright starts webServers before globalSetup), because every
 * webServer migrates the database it is given and serves against that Supabase project. An ambient
 * DATABASE_URL or SUPABASE_URL (a shell profile, an agent sandbox) can point at a hosted project, so:
 *
 * - E2E_DATABASE_URL / E2E_SUPABASE_URL are explicit choices of a test target and are used as given;
 * - otherwise DATABASE_URL / SUPABASE_URL are used only when their host is this machine
 *   (127.0.0.1, localhost or ::1);
 * - any other host is refused with an error, unless E2E_ALLOW_HOSTED=1 says it is deliberate.
 */

type Env = Record<string, string | undefined>;

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** Host of a URL ("" when it has none, e.g. a Unix-socket postgres URL), or null when it does not parse. */
function urlHost(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** True when the URL's host is this machine (127.0.0.1, localhost or ::1). */
export function isLoopbackDatabaseUrl(url: string): boolean {
  const host = urlHost(url);
  return host !== null && LOOPBACK_HOSTS.has(host);
}

function vetted(env: Env, explicitName: string, ambientNames: string[], what: string): string | undefined {
  const explicit = env[explicitName];
  if (explicit) return explicit;
  const name = ambientNames.find((n) => env[n]);
  if (!name) return undefined;
  const url = env[name] as string;
  if (isLoopbackDatabaseUrl(url) || env.E2E_ALLOW_HOSTED === "1") return url;
  const host = urlHost(url);
  // Name the host only: the URL itself can carry a password.
  throw new Error(
    `Refusing to start the e2e servers: ${name} points at host "${host ?? "unparseable URL"}", not this machine, ` +
      `and the servers ${what}. Set ${explicitName} to a test ${explicitName === "E2E_DATABASE_URL" ? "database" : "project"}, ` +
      `use a loopback ${name} (127.0.0.1, localhost or ::1), or set E2E_ALLOW_HOSTED=1 to use this host on purpose.`,
  );
}

/** The database the e2e servers migrate and use, or undefined for a quick run without one. */
export function e2eDatabaseUrl(env: Env): string | undefined {
  return vetted(env, "E2E_DATABASE_URL", ["DATABASE_URL"], "migrate the database they use");
}

/** The Supabase project (auth, storage) the e2e servers and test-user helpers use, or undefined. */
export function e2eSupabaseUrl(env: Env): string | undefined {
  return vetted(env, "E2E_SUPABASE_URL", ["SUPABASE_URL", "VITE_SUPABASE_URL"], "create users in its auth");
}

/**
 * Both targets, checked together so one error run lists the first problem before anything starts. The
 * values are written back as DATABASE_URL / SUPABASE_URL (empty when there is none) for the webServers,
 * the test-user helpers and the workers, so nothing downstream (a .env file, an inherited variable) can
 * supply a different one.
 */
export function e2eTargets(env: Env): { databaseUrl: string; supabaseUrl: string } {
  const databaseUrl = e2eDatabaseUrl(env) ?? "";
  const supabaseUrl = e2eSupabaseUrl(env) ?? "";
  return { databaseUrl, supabaseUrl };
}
