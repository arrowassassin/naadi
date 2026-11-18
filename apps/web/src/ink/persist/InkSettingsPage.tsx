import { ErrorState, Skeleton, Tile } from "@naadi/ui";
import { Check } from "lucide-react";
import { useId } from "react";
import { ApiError } from "../../auth/apiError";
import { useUiText } from "../../copy/useUiText";
import { useOnlineStatus } from "../../health/useOnlineStatus";
import { SettingsNav } from "../../routes/SettingsNav";
import { useHandwritingSearch } from "./handwritingSearch";
import { type InkSettings, useInkSettings } from "./settings";

function SwitchRow({
  name,
  note,
  on,
  disabled,
  onChange,
  detailId,
}: {
  name: string;
  note: string;
  on: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
  /** Another element that describes the switch too (e.g. a privacy note below the row). */
  detailId?: string;
}) {
  const t = useUiText();
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-4 border-line-soft border-t py-4 first:border-t-0">
      <span className="flex min-w-0 flex-col gap-1">
        <span id={`${id}-n`} className="font-semibold text-[1.0625rem] text-text">
          {name}
        </span>
        <span id={`${id}-d`} className="text-text-2 text-ui-sm">
          {note}
        </span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-labelledby={`${id}-n`}
        aria-describedby={detailId ? `${id}-d ${detailId}` : `${id}-d`}
        disabled={disabled}
        onClick={() => onChange(!on)}
        className="flex min-h-11 shrink-0 items-center gap-2 rounded-pill border border-line-control px-2 text-ui-sm disabled:opacity-50"
      >
        <span
          aria-hidden="true"
          className={
            on
              ? "flex h-7 w-12 items-center justify-end rounded-pill bg-paper p-1"
              : "flex h-7 w-12 items-center justify-start rounded-pill bg-line p-1"
          }
        >
          <span className={on ? "size-5 rounded-full bg-ground" : "size-5 rounded-full bg-text-3"} />
        </span>
        <span className="min-w-7 pr-1 text-left font-semibold">{on ? t("settings.on") : t("settings.off")}</span>
      </button>
    </div>
  );
}

/**
 * Handwriting search (segment 13): opt-in, off until the student turns it on. Saved as
 * profiles.settings.handwriting_search (top level, not inside `ink`); the API clears the recognised text when it is
 * turned off and queues the ink already there when it is turned on.
 */
function HandwritingSearchTile({ online }: { online: boolean }) {
  const t = useUiText();
  const id = useId();
  const hw = useHandwritingSearch();
  const err = hw.error;
  return (
    <Tile as="section" aria-labelledby={`${id}-h`} className="flex max-w-2xl flex-col gap-2">
      <h2 id={`${id}-h`} className="m-0 font-display font-bold text-[1.5rem]">
        {t("ink.ocr.group")}
      </h2>
      <div className="flex flex-col">
        <SwitchRow
          name={t("ink.ocr.setting_label")}
          note={t("ink.ocr.setting_note")}
          on={hw.on}
          disabled={!online || hw.saving}
          onChange={hw.set}
          detailId={`${id}-p`}
        />
      </div>
      <p id={`${id}-p`} className="m-0 max-w-prose text-text-2 text-ui-sm">
        {t("ink.ocr.privacy_note")}
      </p>
      <p role="status" className="m-0 flex min-h-6 items-center gap-1.5 text-text-2 text-ui-sm">
        {!online ? (
          t("settings.offline")
        ) : hw.saving ? (
          t("settings.saving")
        ) : err ? null : hw.cleared ? (
          <>
            <Check aria-hidden="true" size={16} />
            {t("ink.ocr.cleared")}
          </>
        ) : hw.on ? (
          t("ink.ocr.state_on")
        ) : (
          t("ink.ocr.state_off")
        )}
      </p>
      {err ? (
        <ErrorState
          title={t("ink.ocr.save_error")}
          message={err.message}
          requestId={err instanceof ApiError ? err.requestId : null}
        />
      ) : null}
    </Tile>
  );
}

/**
 * Settings → Pencil & ink: pen-only drawing and predicted ink, saved to profiles.settings.ink, and the handwriting
 * search opt-in.
 */
export function InkSettingsPage() {
  const t = useUiText();
  const online = useOnlineStatus();
  const ink = useInkSettings();
  const set = (patch: Partial<InkSettings>) => ink.save(patch);
  const err = ink.error;
  return (
    <section aria-labelledby="ink-settings-h" className="flex flex-col gap-8 pt-6 tablet:pt-12">
      <div className="flex flex-col gap-3">
        <span className="font-mono text-label text-text-3 uppercase">{t("account.settings_label")}</span>
        <h1 id="ink-settings-h" className="m-0 font-display text-headline">
          {t("settings.ink_title")}
        </h1>
        <SettingsNav current="ink" />
      </div>
      {!ink.loaded ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <Skeleton className="h-48 rounded-tile" />
        </div>
      ) : (
        <Tile as="section" aria-labelledby="ink-group-h" className="flex max-w-2xl flex-col gap-2">
          <h2 id="ink-group-h" className="m-0 font-display font-bold text-[1.5rem]">
            {t("settings.ink_group")}
          </h2>
          <div className="flex flex-col">
            <SwitchRow
              name={t("settings.ink_pen_only")}
              note={t("settings.ink_pen_only_note")}
              on={ink.settings.pen_only}
              disabled={!online || ink.saving}
              onChange={(pen_only) => set({ pen_only })}
            />
            <SwitchRow
              name={t("settings.ink_predict")}
              note={t("settings.ink_predict_note")}
              on={ink.settings.predict}
              disabled={!online || ink.saving}
              onChange={(predict) => set({ predict })}
            />
          </div>
          <p role="status" className="m-0 flex min-h-6 items-center gap-1.5 text-text-2 text-ui-sm">
            {!online ? (
              t("settings.offline")
            ) : ink.saving ? (
              t("settings.saving")
            ) : err ? null : (
              <>
                <Check aria-hidden="true" size={16} />
                {t("settings.saved_on_account")}
              </>
            )}
          </p>
          {err ? (
            <ErrorState
              title={t("settings.save_failed")}
              message={err.message}
              requestId={err instanceof ApiError ? err.requestId : null}
            />
          ) : null}
        </Tile>
      )}
      {ink.loaded ? <HandwritingSearchTile online={online} /> : null}
    </section>
  );
}
