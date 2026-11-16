import { rmSync } from "node:fs";
import { AUTH_STATE_DIR, canMintUsers, isLive } from "./auth-users";

/**
 * Signed-in tests mint throwaway Supabase users with the service role (locally the hosted project or the
 * CI auth container). Sessions are created lazily per role (see session.ts); this only removes the previous
 * run's storage states, whose users were deleted at teardown, and fails loudly when minting is impossible.
 */
export default async function globalSetup(): Promise<void> {
  rmSync(AUTH_STATE_DIR, { recursive: true, force: true });
  const supabase = process.env.SUPABASE_URL ?? "";
  const loopback = /^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(supabase.replace(/\/$/, ""));
  if (!isLive() && supabase && !loopback && process.env.E2E_ALLOW_HOSTED !== "1") {
    throw new Error(
      "e2e targets the local auth stack (apps/api/tests/auth/auth_stack.sh). SUPABASE_URL points elsewhere: " +
        "set E2E_ALLOW_HOSTED=1 to create throwaway users on a hosted project deliberately.",
    );
  }
  if (!isLive() && !canMintUsers()) {
    throw new Error(
      "e2e needs SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY (hosted project, or the CI auth container)",
    );
  }
}
