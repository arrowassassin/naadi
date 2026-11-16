const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** Host of a postgres URL ("" when it has none, e.g. a Unix socket), without throwing on a malformed URL. */
function dbHost(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** True when the URL's host is this machine (127.0.0.1, localhost or ::1). */
export function isLoopbackDatabaseUrl(url: string): boolean {
  const host = dbHost(url);
  return host !== null && LOOPBACK_HOSTS.has(host);
}

/**
 * The database the e2e servers migrate and use. E2E_DATABASE_URL is an explicit choice of test database.
 * DATABASE_URL is used only when it points at this machine (127.0.0.1, localhost or ::1): every webServer
 * runs migrate_all against it, so an inherited hosted URL (staging, production) must never be picked up
 * by accident. E2E_ALLOW_HOSTED=1 is the deliberate override.
 */
export function e2eDatabaseUrl(env: Record<string, string | undefined>): string | undefined {
  if (env.E2E_DATABASE_URL) return env.E2E_DATABASE_URL;
  const url = env.DATABASE_URL;
  if (!url) return undefined;
  if (isLoopbackDatabaseUrl(url) || env.E2E_ALLOW_HOSTED === "1") return url;
  const host = dbHost(url);
  throw new Error(
    `Refusing to run the e2e servers against DATABASE_URL on host "${host ?? "unparseable URL"}": they migrate ` +
      "the database they use. Point E2E_DATABASE_URL at a test database, use a loopback DATABASE_URL " +
      "(127.0.0.1, localhost or ::1), or set E2E_ALLOW_HOSTED=1 to use this host on purpose.",
  );
}
