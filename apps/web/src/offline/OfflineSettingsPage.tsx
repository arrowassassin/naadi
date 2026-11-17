import { Button, Tile } from "@naadi/ui";
import { CircleCheck, CloudDownload, CloudOff, Download, HardDrive, ShieldCheck, Trash2 } from "lucide-react";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { SettingsNav } from "../routes/SettingsNav";
import { type PartName, type PartSize, useWeekDownload } from "./download";
import { formatBytes, useInstallMode, useStorageState } from "./storage";

const PART_KEYS: Record<PartName, string> = {
  chapters: "offline.part.chapters",
  questions: "offline.part.questions",
  cards: "offline.part.cards",
  due: "offline.part.cards",
  notes: "offline.part.notes",
  notebooks: "offline.part.notebooks",
  ink: "offline.part.ink",
  plan: "offline.part.plan",
  pathway: "offline.part.pathway",
  figures: "offline.part.figures",
  videos: "offline.part.videos",
};

/** "Sun", "Mon" … in the student's locale: the last day the download covers. */
export function dayName(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { weekday: "short" });
}

function Row({ icon: Icon, name, value }: { icon: typeof HardDrive; name: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-line-soft border-t py-4 first:border-t-0">
      <span className="flex min-w-0 items-center gap-3">
        <Icon aria-hidden="true" size={18} className="shrink-0 text-text-2" />
        <span className="font-semibold text-[1.0625rem] text-text">{name}</span>
      </span>
      <span className="text-right text-text-2">{value}</span>
    </div>
  );
}

function Parts({ parts }: { parts: readonly PartSize[] }) {
  const t = useUiText();
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0" data-offline-parts="">
      {parts
        .filter((p) => p.count > 0)
        .map((p) => (
          <li key={p.part} className="flex justify-between gap-3 text-text-2">
            <span>{t(PART_KEYS[p.part] ?? "offline.part.other")}</span>
            <span className="font-mono">{formatBytes(p.bytes)}</span>
          </li>
        ))}
    </ul>
  );
}

/** The download block (Settings → Offline; Today shows a compact version). One lime action at most. */
export function WeekDownloadPanel({ compact = false }: { compact?: boolean }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const dl = useWeekDownload();
  const total = (parts: readonly PartSize[]) => formatBytes(parts.reduce((n, p) => n + p.bytes, 0));

  return (
    <div className="flex flex-col gap-3" data-offline-download={dl.phase}>
      {dl.phase === "ready" && dl.record ? (
        <>
          <p role="status" className="m-0 flex items-center gap-2 font-semibold">
            <CircleCheck aria-hidden="true" size={18} />
            {t("offline.ready_until", { day: dayName(dl.record.end) })}
          </p>
          {compact ? null : <Parts parts={dl.record.parts} />}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void dl.check()} disabled={!online}>
              <CloudDownload aria-hidden="true" size={18} />
              {t("offline.download_again")}
            </Button>
            {compact ? null : (
              <Button onClick={() => void dl.remove()}>
                <Trash2 aria-hidden="true" size={18} />
                {t("offline.remove_download")}
              </Button>
            )}
          </div>
        </>
      ) : dl.phase === "preview" && dl.preview ? (
        <>
          <p className="m-0 text-text-2">{t("offline.download_size", { size: total(dl.preview) })}</p>
          {compact ? null : <Parts parts={dl.preview} />}
          <Button
            variant={compact ? "secondary" : "primary"}
            size={compact ? undefined : "cta"}
            onClick={() => void dl.start()}
            disabled={!online}
          >
            <Download aria-hidden="true" size={18} />
            {t("offline.download_start")}
          </Button>
        </>
      ) : dl.phase === "downloading" ? (
        <p role="status" aria-live="polite" className="m-0 flex items-center gap-2 font-semibold">
          <CloudDownload aria-hidden="true" size={18} className="motion-safe:animate-pulse" />
          {dl.progress
            ? t("offline.download_progress", {
                part: t(PART_KEYS[dl.progress.part]),
                done: String(dl.progress.done),
                total: String(dl.progress.total),
              })
            : t("offline.downloading")}
        </p>
      ) : (
        <>
          {dl.phase === "failed" ? (
            <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
              {t("offline.download_failed")}
            </p>
          ) : null}
          {online ? (
            <Button
              variant={compact ? "secondary" : "primary"}
              size={compact ? undefined : "cta"}
              onClick={() => void dl.check()}
              disabled={dl.phase === "checking"}
            >
              <CloudDownload aria-hidden="true" size={18} />
              {dl.phase === "checking" ? t("offline.checking") : t("offline.download_week")}
            </Button>
          ) : (
            <p className="m-0 flex items-center gap-2 text-text-2">
              <CloudOff aria-hidden="true" size={18} />
              {t("offline.download_needs_connection")}
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** Settings → Offline (design/Settings.dc.html, Offline group). */
export function OfflineSettingsPage() {
  const t = useUiText();
  const storage = useStorageState();
  const install = useInstallMode();
  const used =
    storage.usage === null
      ? t("offline.size_unknown")
      : storage.quota
        ? t("offline.stored_value", { used: formatBytes(storage.usage), quota: formatBytes(storage.quota) })
        : formatBytes(storage.usage);
  return (
    <section
      aria-labelledby="offline-settings-h"
      className="flex flex-col gap-8 pt-6 tablet:pt-12"
      data-offline-settings=""
    >
      <div className="flex flex-col gap-3">
        <span className="font-mono text-label text-text-3 uppercase">{t("account.settings_label")}</span>
        <h1 id="offline-settings-h" className="m-0 font-display text-headline">
          {t("offline.group_title")}
        </h1>
        <SettingsNav current="offline" />
      </div>
      <Tile as="section" aria-labelledby="offline-dl-h" className="flex max-w-2xl flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id="offline-dl-h" className="m-0 font-display font-bold text-[1.5rem]">
            {t("offline.download_week")}
          </h2>
          <p className="m-0 text-text-2">{t("offline.download_note")}</p>
        </div>
        <WeekDownloadPanel />
      </Tile>
      <Tile as="section" aria-labelledby="offline-device-h" className="flex max-w-2xl flex-col gap-2">
        <h2 id="offline-device-h" className="m-0 font-display font-bold text-[1.5rem]">
          {t("offline.device_title")}
        </h2>
        <div className="flex flex-col">
          <Row icon={HardDrive} name={t("offline.stored")} value={used} />
          <Row
            icon={ShieldCheck}
            name={t("offline.persisted")}
            value={
              storage.persisted === null
                ? t("offline.size_unknown")
                : storage.persisted
                  ? t("offline.persisted_yes")
                  : t("offline.persisted_no")
            }
          />
        </div>
        {storage.persisted === false ? (
          <Button className="self-start" onClick={() => void storage.persist()}>
            {t("offline.persist_ask")}
          </Button>
        ) : null}
        {install.mode === "prompt" ? (
          <Button className="self-start" onClick={() => void install.install()}>
            {t("offline.install")}
          </Button>
        ) : install.mode === "ios" ? (
          <p className="m-0 text-text-2" data-install-hint="">
            {t("offline.install_hint_ios")}
          </p>
        ) : null}
      </Tile>
    </section>
  );
}
