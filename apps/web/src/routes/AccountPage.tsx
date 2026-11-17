import type { MeResponse } from "@naadi/shared";
import { Button, Chip, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Check, ChevronDown, LogOut, Shield } from "lucide-react";
import { type FormEvent, useId, useMemo, useState } from "react";
import { useMe, useUpdateMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { type SignOutScope, signOut } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { StudyPlanSettings } from "../planner/StudyPlanSettings";
import { YouTubePrivacyNotice } from "../video/PrivacyNotice";
import { SettingsNav } from "./SettingsNav";

function timeZones(current: string): string[] {
  let all: string[] = [];
  try {
    all = Intl.supportedValuesOf("timeZone");
  } catch {
    all = [];
  }
  const set = new Set(all);
  set.add(current);
  set.add(Intl.DateTimeFormat().resolvedOptions().timeZone);
  return [...set].filter(Boolean).sort((a, b) => a.localeCompare(b));
}

const INPUT =
  "min-h-14 w-full rounded-swatch border border-line-control bg-ground px-[18px] text-[1.0625rem] text-text disabled:text-text-muted aria-invalid:border-amber";

function ProfileForm({ me }: { me: MeResponse }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const update = useUpdateMe();
  const [name, setName] = useState(me.profile.display_name ?? "");
  const [tz, setTz] = useState(me.profile.timezone);
  const [saved, setSaved] = useState(false);
  const zones = useMemo(() => timeZones(me.profile.timezone), [me.profile.timezone]);
  const nameId = useId();
  const tzId = useId();
  const deviceTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const trimmed = name.trim().replace(/\s+/g, " ");
  const dirty = trimmed !== (me.profile.display_name ?? "") || tz !== me.profile.timezone;
  const tooLong = trimmed.length > 120;

  function onSubmit(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    if (!dirty || tooLong) return;
    setSaved(false);
    update.mutate(
      { display_name: trimmed || null, timezone: tz },
      {
        onSuccess: () => setSaved(true),
      },
    );
  }

  const err = update.error;
  return (
    <form noValidate onSubmit={onSubmit} className="flex flex-col gap-6" aria-busy={update.isPending}>
      <label htmlFor={nameId} className="flex flex-col gap-2 text-[0.875rem] text-text-2">
        {t("account.display_name")}
        <input
          id={nameId}
          className={INPUT}
          value={name}
          maxLength={160}
          autoComplete="name"
          aria-invalid={tooLong || undefined}
          onChange={(e) => {
            setName(e.target.value);
            setSaved(false);
          }}
        />
        {tooLong ? <span className="text-amber">{t("account.name_too_long")}</span> : null}
      </label>
      <label htmlFor={tzId} className="flex flex-col gap-2 text-[0.875rem] text-text-2">
        {t("account.time_zone")}
        <span className="relative flex">
          <select
            id={tzId}
            className={`${INPUT} appearance-none pr-12`}
            value={tz}
            onChange={(e) => {
              setTz(e.target.value);
              setSaved(false);
            }}
          >
            {zones.map((z) => (
              <option key={z} value={z}>
                {z.replaceAll("_", " ")}
              </option>
            ))}
          </select>
          <ChevronDown
            aria-hidden="true"
            size={18}
            className="pointer-events-none absolute top-1/2 right-[18px] -translate-y-1/2 text-text-2"
          />
        </span>
        {deviceTz && deviceTz !== tz ? (
          <span className="text-text-3">
            {t("account.device_tz", { tz: deviceTz.replaceAll("_", " ") })}{" "}
            <button
              type="button"
              className="min-h-11 cursor-pointer border-0 bg-transparent p-0 text-text underline underline-offset-4"
              onClick={() => setTz(deviceTz)}
            >
              {t("account.use_device_tz")}
            </button>
          </span>
        ) : null}
      </label>
      {err ? (
        <ErrorState
          title={t("account.save_failed_title")}
          message={err.message}
          requestId={err instanceof ApiError ? err.requestId : null}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" variant="primary" size="cta" disabled={!dirty || tooLong || update.isPending || !online}>
          {t(update.isPending ? "account.saving" : "account.save")}
        </Button>
        {saved && !dirty ? (
          <span role="status" className="inline-flex items-center gap-2 text-text-2">
            <Check aria-hidden="true" size={18} /> {t("account.saved")}
          </span>
        ) : null}
        {!online ? <span className="text-text-3">{t("account.offline_save")}</span> : null}
      </div>
    </form>
  );
}

function SignOutPanel() {
  const navigate = useNavigate();
  const t = useUiText();
  const [busy, setBusy] = useState<SignOutScope | null>(null);
  const [confirmGlobal, setConfirmGlobal] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function run(scope: SignOutScope): Promise<void> {
    setBusy(scope);
    setMessage(null);
    const r = await signOut(scope);
    setBusy(null);
    if (!r.ok) {
      setMessage(r.message ?? t("account.sign_out_failed"));
      return;
    }
    void navigate({ to: "/sign-in", search: { signed_out: 1 }, replace: true });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <Button
          variant="secondary"
          icon={<LogOut size={16} />}
          disabled={busy !== null}
          onClick={() => void run("local")}
        >
          {t(busy === "local" ? "account.signing_out" : "account.sign_out")}
        </Button>
        {confirmGlobal ? null : (
          <Button variant="secondary" disabled={busy !== null} onClick={() => setConfirmGlobal(true)}>
            {t("account.sign_out_all")}
          </Button>
        )}
      </div>
      {confirmGlobal ? (
        <div className="flex flex-col gap-3 rounded-block border border-line-strong bg-surface-2 p-4">
          <p className="m-0 text-text">{t("account.sign_out_all_confirm")}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="strong" disabled={busy !== null} onClick={() => void run("global")}>
              {t(busy === "global" ? "account.signing_out_everywhere" : "account.sign_out_everywhere")}
            </Button>
            <Button variant="secondary" disabled={busy !== null} onClick={() => setConfirmGlobal(false)}>
              {t("account.cancel")}
            </Button>
          </div>
        </div>
      ) : null}
      {message ? <ErrorState title={t("account.sign_out_failed_title")} message={message} /> : null}
    </div>
  );
}

export function AccountPage() {
  const t = useUiText();
  const me = useMe();
  return (
    <section aria-labelledby="account-h" className="flex flex-col gap-8 pt-6 tablet:pt-12">
      <div className="flex flex-col gap-3">
        <span className="font-mono text-label text-text-3 uppercase">{t("account.settings_label")}</span>
        <h1 id="account-h" className="m-0 font-display text-headline">
          {t("account.heading")}
        </h1>
        <SettingsNav current="account" />
      </div>

      {me.isPending && !me.data ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <p role="status" className="sr-only">
            {t("account.loading")}
          </p>
          <Skeleton className="h-48 rounded-tile" />
          <Skeleton className="h-32 rounded-tile" />
        </div>
      ) : !me.data ? (
        <ErrorState
          title={t("account.load_failed_title")}
          message={me.error?.message ?? t("common.error_fallback")}
          requestId={me.error instanceof ApiError ? me.error.requestId : null}
          onRetry={() => void me.refetch()}
          retrying={me.isFetching}
        />
      ) : (
        <>
          <div className="grid items-start gap-5 laptop:grid-cols-[2fr_1fr]">
            <Tile as="section" aria-labelledby="profile-h" className="flex flex-col gap-6">
              <h2 id="profile-h" className="m-0 font-display font-bold text-[1.5rem]">
                {t("account.profile_heading")}
              </h2>
              <ProfileForm key={me.data.profile.user_id} me={me.data} />
            </Tile>
            <div className="flex flex-col gap-5">
              <Tile as="section" aria-labelledby="signin-h" className="flex flex-col gap-4">
                <TileLabel>{t("account.signed_in_as")}</TileLabel>
                <h2 id="signin-h" className="m-0 break-all font-semibold text-[1.0625rem]">
                  {me.data.email ?? t("account.no_email")}
                </h2>
                <div className="flex flex-wrap gap-2">
                  <Chip tone={me.data.email_verified ? "done" : "warning"}>
                    {t(me.data.email_verified ? "account.email_verified" : "account.email_unverified")}
                  </Chip>
                  {me.data.is_admin ? <Chip tone="meta">{t("account.admin_chip")}</Chip> : null}
                </div>
                {/* The one place the admin console is linked from; non-admins never see it (the API also 403s). */}
                {me.data.is_admin ? (
                  <Link
                    to="/admin"
                    data-testid="admin-link"
                    className="inline-flex min-h-11 items-center gap-2 self-start text-text"
                  >
                    <Shield aria-hidden="true" size={16} />
                    {t("admin.account_link")}
                  </Link>
                ) : null}
              </Tile>
              <Tile as="section" aria-labelledby="signout-h" className="flex flex-col gap-4">
                <h2 id="signout-h" className="m-0 font-display font-bold text-[1.5rem]">
                  {t("account.sign_out_heading")}
                </h2>
                <SignOutPanel />
              </Tile>
              <YouTubePrivacyNotice />
            </div>
          </div>
          <StudyPlanSettings />
        </>
      )}
    </section>
  );
}
