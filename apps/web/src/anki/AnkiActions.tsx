import { Button } from "@naadi/ui";
import { Download, Upload } from "lucide-react";
import { useState } from "react";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { useAnkiEnabled } from "./api";
import { ExportDialog } from "./ExportDialog";
import { ImportDialog } from "./ImportDialog";

/** "Import deck" / "Export deck" with their dialogs. Absent while the `anki` flag is off; disabled offline. */
export function AnkiActions({ onReview }: { onReview: () => void }) {
  const t = useUiText();
  const on = useAnkiEnabled();
  const online = useOnlineStatus();
  const [dialog, setDialog] = useState<"import" | "export" | null>(null);
  if (!on) return null;
  return (
    <>
      <Button variant="secondary" icon={<Upload size={18} />} onClick={() => setDialog("import")} disabled={!online}>
        {t("anki.import_action")}
      </Button>
      <Button variant="secondary" icon={<Download size={18} />} onClick={() => setDialog("export")} disabled={!online}>
        {t("anki.export_action")}
      </Button>
      <ImportDialog open={dialog === "import"} onClose={() => setDialog(null)} onReview={onReview} />
      <ExportDialog open={dialog === "export"} onClose={() => setDialog(null)} />
    </>
  );
}
