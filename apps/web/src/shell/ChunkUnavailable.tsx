import { Button } from "@naadi/ui";
import { CloudOff, RefreshCw } from "lucide-react";
import { useEffect } from "react";
import { useUiText } from "../copy/useUiText";

/**
 * What a lazily loaded part of the app shows when its code could not be fetched (see lazyWithFallback): words, an
 * icon and a Try again button (status never by colour alone). It retries by itself when the device comes back
 * online. The rest of the app (shell, navigation, other pages) keeps working.
 */
export function ChunkUnavailable({ variant, onRetry }: { variant: "page" | "inline"; onRetry: () => void }) {
  const t = useUiText();
  useEffect(() => {
    window.addEventListener("online", onRetry);
    return () => window.removeEventListener("online", onRetry);
  }, [onRetry]);
  const retry = (
    <Button
      variant="secondary"
      onClick={onRetry}
      icon={<RefreshCw size={16} aria-hidden="true" />}
      className="min-h-11"
    >
      {t("shell.chunk_retry")}
    </Button>
  );
  if (variant === "inline") {
    return (
      <p role="status" data-chunk-unavailable="inline" className="m-0 flex flex-wrap items-center gap-3 text-text-2">
        <CloudOff size={16} aria-hidden="true" />
        <span>{t("shell.chunk_offline_inline")}</span>
        {retry}
      </p>
    );
  }
  return (
    <section role="status" data-chunk-unavailable="page" className="flex max-w-xl flex-col items-start gap-3 pt-10">
      <CloudOff size={28} aria-hidden="true" className="text-text-2" />
      <h1 className="m-0 font-display text-headline">{t("shell.chunk_offline_title")}</h1>
      <p className="m-0 text-text-2">{t("shell.chunk_offline_body")}</p>
      {retry}
    </section>
  );
}
