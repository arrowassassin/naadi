import type { TaskOut } from "@naadi/shared";
import { Chip, type ChipTone } from "@naadi/ui";
import { Check, CircleDashed, CircleDot, Minus, RefreshCw } from "lucide-react";
import { useUiText } from "../copy/useUiText";

export const STATUSES = ["todo", "doing", "done", "na"] as const;
export type Status = (typeof STATUSES)[number];

const TONE: Record<Status, ChipTone> = { todo: "planned", doing: "current", done: "done", na: "meta" };
function StatusIcon({ status }: { status: Status }) {
  switch (status) {
    case "todo":
      return <CircleDashed size={13} />;
    case "doing":
      return <CircleDot size={13} />;
    case "done":
      return <Check size={13} />;
    default:
      return <Minus size={13} />;
  }
}

/** A task's status as a word with an icon: never colour alone. */
export function StatusChip({ status }: { status: Status }) {
  const t = useUiText();
  return (
    <Chip tone={TONE[status]} icon={<StatusIcon status={status} />}>
      {t(`pathway.status.${status}`)}
    </Chip>
  );
}

/** "Re-verify": amber, a word and an icon, with the reason in the accessible name. */
export function ReverifyBadge({ reason }: { reason: NonNullable<TaskOut["reverify"]> }) {
  const t = useUiText();
  return (
    <Chip tone="warning" icon={<RefreshCw size={13} />} srPrefix={t(`pathway.reverify_reason.${reason}`)}>
      {t("pathway.reverify")}
    </Chip>
  );
}
