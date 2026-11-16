import { rmSync } from "node:fs";
import { AUTH_STATE_DIR, canMintUsers, deleteUser, type E2ERole, ROLES } from "./auth-users";

/** Delete every throwaway test user of this run (their profiles go with them: on delete cascade). */
export default async function globalTeardown(): Promise<void> {
  rmSync(AUTH_STATE_DIR, { recursive: true, force: true });
  if (!canMintUsers()) return;
  for (const role of ROLES as readonly E2ERole[]) deleteUser(role);
}
