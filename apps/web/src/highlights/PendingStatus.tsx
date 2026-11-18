import { usePendingOps } from "../notes/api";
import { NoteSaveStatus } from "../notes/SaveStatus";

/** The save state of these notes, shown only while one of them has an unsent or refused change. */
export function PendingStatus({ ids, className }: { ids: readonly string[]; className?: string }) {
  const ops = usePendingOps();
  if (!ops.some((o) => ids.includes(o.id))) return null;
  return <NoteSaveStatus ids={ids} className={className} />;
}
