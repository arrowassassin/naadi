import { Button } from "@naadi/ui";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { onUpdateReady } from "../pwa/register";

/** "New version ready · Reload" (segment 18). Hidden while a timed/exam session runs: no reload mid-exam. */
export function UpdatePrompt({ hidden }: { hidden: boolean }) {
  const t = useUiText();
  const [apply, setApply] = useState<(() => void) | null>(null);
  useEffect(() => onUpdateReady((fn) => setApply(() => fn)), []);
  if (!apply || hidden) return null;
  return (
    <div
      role="status"
      data-update-ready=""
      className="flex min-h-11 items-center gap-3 rounded-pill border border-line-strong bg-surface-2 px-4 py-1.5 text-text"
    >
      <RefreshCw aria-hidden="true" size={18} className="shrink-0 text-text-2" />
      <span className="flex-1 font-semibold">{t("offline.update_ready")}</span>
      <Button onClick={apply}>{t("offline.update_reload")}</Button>
    </div>
  );
}
