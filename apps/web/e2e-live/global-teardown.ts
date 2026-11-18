import { spawnSync } from "node:child_process";
import path from "node:path";

/**
 * After every live-auth run: scan everything the run wrote for the value of each secret it had (raw, URL-encoded,
 * base64; inside zips too), with scripts/scan_artifacts.py. Any match fails the run, and the file is named so it can
 * be deleted; the value is never printed. Segment 22, lead decision 20261117T060320.
 */
const repoRoot = path.resolve(import.meta.dirname, "../../..");
export const SECRET_ENVS = [
  "HF_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_JWT_SECRET",
  "SUPABASE_ACCESS_TOKEN",
  "ADMIN_EMAILS",
  "DATABASE_URL",
] as const;
export const SCANNED = ["test-results/playwright-live-auth", "test-results/screens/03-live"];

export default function globalTeardown(): void {
  const found: string[] = [];
  for (const name of SECRET_ENVS) {
    const value = process.env[name];
    if (!value || value.length < 8) continue; // unset here: nothing of it can have been written
    const r = spawnSync("python3", ["scripts/scan_artifacts.py", "--env", name, ...SCANNED], {
      cwd: repoRoot,
      encoding: "utf8",
    });
    if (r.status !== 0) found.push(`${name}: ${(r.stdout || r.stderr).trim()}`);
  }
  if (found.length) {
    throw new Error(`secret values found in live-auth test results (delete those files):\n${found.join("\n")}`);
  }
}
