import { type FullConfig, request as pwRequest } from "@playwright/test";
import { deleteUser, type E2ERole, isLive, ROLES, storedAccessToken } from "./auth-users";

/**
 * Delete every throwaway test user of this run (their profiles go with them: on delete cascade). Against
 * the live Space the owner's account is kept, and only the session this run created for it is revoked.
 */
export default async function globalTeardown(config: FullConfig): Promise<void> {
  const use = config.projects[0]?.use ?? {};
  if (isLive()) {
    const api = await pwRequest.newContext({ baseURL: use.baseURL, extraHTTPHeaders: use.extraHTTPHeaders });
    try {
      const cfg = (await (await api.get("/api/public-config")).json()) as {
        supabase_url: string;
        supabase_anon_key: string;
      };
      const token = storedAccessToken("admin");
      await api.post(`${cfg.supabase_url}/auth/v1/logout?scope=local`, {
        headers: { apikey: cfg.supabase_anon_key, authorization: `Bearer ${token}` },
      });
    } catch {
      // No stored owner session (setup failed before signing in): nothing to revoke.
    } finally {
      await api.dispose();
    }
  }
  for (const role of ROLES as readonly E2ERole[]) deleteUser(role);
}
