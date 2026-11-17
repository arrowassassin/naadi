import type { IngestStorageOut } from "@naadi/shared";
import { cx } from "@naadi/ui";
import { useUiText } from "../copy/useUiText";
import { sizeLabel } from "./model";

/** Storage used against the student's limit: a meter with the numbers in words (never the bar alone). */
export function StorageMeter({ storage, className }: { storage: IngestStorageOut; className?: string }) {
  const t = useUiText();
  const limit = Math.max(1, storage.limit_bytes);
  const pct = Math.min(100, Math.round((storage.used_bytes / limit) * 100));
  const vars = {
    used: sizeLabel(t, storage.used_bytes),
    limit: sizeLabel(t, storage.limit_bytes),
    remaining: sizeLabel(t, storage.remaining_bytes),
    pct: String(pct),
  };
  const full = storage.remaining_bytes <= 0;
  return (
    <div className={cx("flex flex-col gap-2", className)} data-storage-meter="">
      <span id="library-storage-label" className="text-ui-sm text-violet-text">
        {t("library.storage_used", vars)}
      </span>
      {/* A styled bar with its numbers in words: a native <meter> cannot be styled the same in WebKit. */}
      {/* biome-ignore lint/a11y/useSemanticElements: see above */}
      <span
        role="meter"
        aria-labelledby="library-storage-label"
        aria-valuemin={0}
        aria-valuemax={storage.limit_bytes}
        aria-valuenow={Math.min(storage.used_bytes, storage.limit_bytes)}
        aria-valuetext={t("library.storage_value", vars)}
        className="block h-2 overflow-hidden rounded-pill bg-violet-line"
      >
        <span
          className={cx("block h-full rounded-pill", full ? "bg-amber" : "bg-violet-text")}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-text-2 text-ui-sm">
        {full ? t("library.storage_full") : t("library.storage_remaining", vars)}
      </span>
    </div>
  );
}
