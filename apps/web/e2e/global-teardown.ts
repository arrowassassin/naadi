import { deleteUser, E2E_USERS, type E2ERole } from "./auth-users";

/** Delete every test user this run created (their profiles go with them: on delete cascade). */
export default async function globalTeardown(): Promise<void> {
  for (const role of Object.keys(E2E_USERS) as E2ERole[]) deleteUser(role);
}
