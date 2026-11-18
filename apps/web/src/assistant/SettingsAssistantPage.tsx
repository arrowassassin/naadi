import { Button, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Check, Trash2, X } from "lucide-react";
import { useState } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { SettingsPage } from "../settings/SettingsPage";
import { useDecideSuggestion, useDeleteMemory, useMemories } from "./api";
import type { MemoryItem } from "./protocol";
import { formatWhen } from "./time";

/*
 * Settings → Assistant: what the assistant remembers about the student (short facts they approved, kept in the
 * assistant's own store), each deletable, and the facts it suggests remembering, each kept or discarded.
 */

function MemoryRow({ m, offline }: { m: MemoryItem; offline: boolean }) {
  const t = useUiText();
  const del = useDeleteMemory();
  const [confirm, setConfirm] = useState(false);
  return (
    <li className="flex flex-col gap-2 border-line-soft border-t py-3 first:border-t-0" data-memory={m.key}>
      <div className="flex items-start justify-between gap-3">
        <span className="flex min-w-0 flex-col gap-1">
          <span className="break-words text-text">{m.fact}</span>
          <span className="text-text-3 text-ui-sm">
            {t("settings.assistant.saved_on", { when: formatWhen(m.created_at) })}
          </span>
        </span>
        {confirm ? null : (
          <button
            type="button"
            onClick={() => setConfirm(true)}
            disabled={offline}
            aria-label={t("settings.assistant.delete_named", { fact: m.fact })}
            className="flex size-11 shrink-0 items-center justify-center rounded-pill text-text-2 hover:bg-surface-2 hover:text-text disabled:text-text-muted"
          >
            <Trash2 aria-hidden="true" size={18} />
          </button>
        )}
      </div>
      {confirm ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-text-2 text-ui-sm">{t("settings.assistant.delete_confirm")}</span>
          <Button
            variant="secondary"
            className="border-amber text-amber"
            icon={<Trash2 size={16} />}
            disabled={del.isPending || offline}
            onClick={() => del.mutate(m.key)}
          >
            {t("settings.assistant.delete")}
          </Button>
          <Button variant="secondary" disabled={del.isPending} onClick={() => setConfirm(false)}>
            {t("settings.assistant.keep_memory")}
          </Button>
        </div>
      ) : null}
      {del.isError ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t("settings.assistant.delete_failed")}
        </p>
      ) : null}
    </li>
  );
}

function SuggestionRow({ m, offline }: { m: MemoryItem; offline: boolean }) {
  const t = useUiText();
  const decide = useDecideSuggestion();
  return (
    <li className="flex flex-col gap-2 border-line-soft border-t py-3 first:border-t-0" data-suggestion={m.key}>
      <span className="break-words text-text">{m.fact}</span>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="assistant"
          icon={<Check size={16} />}
          disabled={decide.isPending || offline}
          onClick={() => decide.mutate({ key: m.key, decision: "keep" })}
        >
          {t("settings.assistant.suggestion_keep")}
        </Button>
        <Button
          variant="secondary"
          icon={<X size={16} />}
          disabled={decide.isPending || offline}
          onClick={() => decide.mutate({ key: m.key, decision: "discard" })}
        >
          {t("settings.assistant.suggestion_discard")}
        </Button>
      </div>
      {decide.isError ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t("settings.assistant.decide_failed")}
        </p>
      ) : null}
    </li>
  );
}

export function SettingsAssistantPage() {
  const t = useUiText();
  const me = useMe();
  const online = useOnlineStatus();
  const on = me.data?.features.assistant === true;
  const memories = useMemories(on);
  const offline = !online;
  return (
    // S21's Settings frame: eyebrow, title, the group nav; it shows "not available" while the assistant is off.
    <SettingsPage group="assistant" titleKey="settings.assistant.title" headingId="settings-assistant-h">
      <div className="flex flex-col gap-6 pb-10">
        {offline ? <p className="m-0 text-text-2">{t("settings.offline")}</p> : null}
        {!on ? null : memories.isPending && !memories.data ? (
          <Skeleton className="h-40 w-full rounded-tile" />
        ) : memories.isError && !memories.data ? (
          <ErrorState
            title={t("settings.assistant.load_failed")}
            message={
              memories.error instanceof ApiError && memories.error.status === 409
                ? t("agent.exam_mode")
                : t("agent.error.generic")
            }
            requestId={memories.error instanceof ApiError ? memories.error.requestId : null}
            onRetry={() => void memories.refetch()}
            retryLabel={t("agent.error.retry")}
          />
        ) : memories.data ? (
          <>
            <Tile className="flex flex-col gap-3">
              <TileLabel>{t("settings.assistant.memories")}</TileLabel>
              <p className="m-0 text-text-2 text-ui-sm">
                {t("settings.assistant.memories_note", {
                  count: String(memories.data.items.length),
                  max: String(memories.data.max_items),
                })}
              </p>
              {memories.data.items.length ? (
                <ul className="m-0 list-none p-0">
                  {memories.data.items.map((m) => (
                    <MemoryRow key={m.key} m={m} offline={offline} />
                  ))}
                </ul>
              ) : (
                <p className="m-0 text-text-2">{t("settings.assistant.memories_empty")}</p>
              )}
            </Tile>
            {memories.data.suggestions.length ? (
              <Tile variant="assistant" className="flex flex-col gap-3">
                <TileLabel variant="assistant">{t("settings.assistant.suggestions")}</TileLabel>
                <p className="m-0 text-text-2 text-ui-sm">{t("settings.assistant.suggestions_note")}</p>
                <ul className="m-0 list-none p-0">
                  {memories.data.suggestions.map((m) => (
                    <SuggestionRow key={m.key} m={m} offline={offline} />
                  ))}
                </ul>
              </Tile>
            ) : null}
          </>
        ) : null}
      </div>
    </SettingsPage>
  );
}
