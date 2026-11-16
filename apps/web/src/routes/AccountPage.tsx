import type { MeResponse } from "@naadi/shared";
import { Button, Chip, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { useNavigate } from "@tanstack/react-router";
import { Check, ChevronDown, LogOut } from "lucide-react";
import { type FormEvent, useId, useMemo, useState } from "react";
import { useMe, useUpdateMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { type SignOutScope, signOut } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";

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
        Display name
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
        {tooLong ? <span className="text-amber">Use at most 120 characters.</span> : null}
      </label>
      <label htmlFor={tzId} className="flex flex-col gap-2 text-[0.875rem] text-text-2">
        Time zone
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
            This device is set to {deviceTz.replaceAll("_", " ")}.{" "}
            <button
              type="button"
              className="min-h-11 cursor-pointer border-0 bg-transparent p-0 text-text underline underline-offset-4"
              onClick={() => setTz(deviceTz)}
            >
              Use it
            </button>
          </span>
        ) : null}
      </label>
      {err ? (
        <ErrorState
          title="Your changes were not saved"
          message={err.message}
          requestId={err instanceof ApiError ? err.requestId : null}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" variant="primary" size="cta" disabled={!dirty || tooLong || update.isPending || !online}>
          {update.isPending ? "Saving…" : "Save changes"}
        </Button>
        {saved && !dirty ? (
          <span role="status" className="inline-flex items-center gap-2 text-text-2">
            <Check aria-hidden="true" size={18} /> Saved
          </span>
        ) : null}
        {!online ? <span className="text-text-3">Connect to save changes.</span> : null}
      </div>
    </form>
  );
}

function SignOutPanel() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<SignOutScope | null>(null);
  const [confirmGlobal, setConfirmGlobal] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function run(scope: SignOutScope): Promise<void> {
    setBusy(scope);
    setMessage(null);
    const r = await signOut(scope);
    setBusy(null);
    if (!r.ok) {
      setMessage(r.message ?? "Could not sign out. Try again.");
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
          {busy === "local" ? "Signing out…" : "Sign out"}
        </Button>
        {confirmGlobal ? null : (
          <Button variant="secondary" disabled={busy !== null} onClick={() => setConfirmGlobal(true)}>
            Sign out of all devices
          </Button>
        )}
      </div>
      {confirmGlobal ? (
        <div className="flex flex-col gap-3 rounded-block border border-line-strong bg-surface-2 p-4">
          <p className="m-0 text-text">
            This signs you out on every device, including installed apps. You will need a new sign-in link on each.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button variant="strong" disabled={busy !== null} onClick={() => void run("global")}>
              {busy === "global" ? "Signing out everywhere…" : "Sign out everywhere"}
            </Button>
            <Button variant="secondary" disabled={busy !== null} onClick={() => setConfirmGlobal(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
      {message ? <ErrorState title="Not signed out" message={message} /> : null}
    </div>
  );
}

export function AccountPage() {
  const me = useMe();
  return (
    <section aria-labelledby="account-h" className="flex flex-col gap-8 pt-6 tablet:pt-12">
      <div className="flex flex-col gap-3">
        <span className="font-mono text-label text-text-3 uppercase">Settings</span>
        <h1 id="account-h" className="m-0 font-display text-headline">
          Account
        </h1>
      </div>

      {me.isPending && !me.data ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <p role="status" className="sr-only">
            Loading your account…
          </p>
          <Skeleton className="h-48 rounded-tile" />
          <Skeleton className="h-32 rounded-tile" />
        </div>
      ) : !me.data ? (
        <ErrorState
          title="Your account could not be loaded"
          message={me.error?.message ?? "Something went wrong."}
          requestId={me.error instanceof ApiError ? me.error.requestId : null}
          onRetry={() => void me.refetch()}
          retrying={me.isFetching}
        />
      ) : (
        <div className="grid items-start gap-5 laptop:grid-cols-[2fr_1fr]">
          <Tile as="section" aria-labelledby="profile-h" className="flex flex-col gap-6">
            <h2 id="profile-h" className="m-0 font-display font-bold text-[1.5rem]">
              Profile
            </h2>
            <ProfileForm key={me.data.profile.user_id} me={me.data} />
          </Tile>
          <div className="flex flex-col gap-5">
            <Tile as="section" aria-labelledby="signin-h" className="flex flex-col gap-4">
              <TileLabel>Signed in as</TileLabel>
              <h2 id="signin-h" className="m-0 break-all font-semibold text-[1.0625rem]">
                {me.data.email ?? "No email on this account"}
              </h2>
              <div className="flex flex-wrap gap-2">
                <Chip tone={me.data.email_verified ? "done" : "warning"}>
                  {me.data.email_verified ? "Email verified" : "Email not verified"}
                </Chip>
                {me.data.is_admin ? <Chip tone="meta">Admin</Chip> : null}
              </div>
            </Tile>
            <Tile as="section" aria-labelledby="signout-h" className="flex flex-col gap-4">
              <h2 id="signout-h" className="m-0 font-display font-bold text-[1.5rem]">
                Sign out
              </h2>
              <SignOutPanel />
            </Tile>
          </div>
        </div>
      )}
    </section>
  );
}
