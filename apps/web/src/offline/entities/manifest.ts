import { getOfflineWeek } from "@naadi/shared";
import { authed } from "../../auth/api";
import { bindManifest, type WeekManifest } from "../download";

/** "Download this week" reads GET /api/offline/week through the generated SDK (no answer field for an unattempted question). */
bindManifest(
  (start) =>
    authed((headers) =>
      getOfflineWeek({ headers, query: start ? { start } : undefined }),
    ) as unknown as Promise<WeekManifest>,
);
