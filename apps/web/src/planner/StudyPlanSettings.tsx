import { Button, Chip, ErrorState, Skeleton, Tile } from "@naadi/ui";
import { Bell, CalendarPlus, Check, Copy, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  currentEndpoint,
  permissionState,
  pushSupport,
  subscribeThisDevice,
  unsubscribeThisDevice,
  webcalUrl,
} from "./pushClient";
import {
  useAddDevice,
  useCalendarFeed,
  useDevices,
  usePushKey,
  useRemoveDevice,
  useRevokeFeed,
  useRotateFeed,
  useSendTest,
} from "./studyPlanApi";

type T = (key: string, vars?: Readonly<Record<string, string>>) => string;

function fmtDate(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(iso));
}

/** A confirm that sits where the button was (no window.confirm). */
function InlineConfirm({
  text,
  yes,
  no,
  onYes,
  onNo,
  busy,
}: {
  text: string;
  yes: string;
  no: string;
  onYes: () => void;
  onNo: () => void;
  busy: boolean;
}) {
  return (
    <fieldset
      aria-label={text}
      className="flex flex-col gap-3 rounded-block border border-line-strong bg-surface-2 p-4 m-0 min-w-0"
    >
      <p className="m-0 text-text">{text}</p>
      <div className="flex flex-wrap gap-3">
        <Button variant="strong" disabled={busy} onClick={onYes}>
          {yes}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onNo}>
          {no}
        </Button>
      </div>
    </fieldset>
  );
}

function CalendarFeed() {
  const t = useUiText();
  const online = useOnlineStatus();
  const feed = useCalendarFeed();
  const rotate = useRotateFeed();
  const revoke = useRevokeFeed();
  const [url, setUrl] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"rotate" | "revoke" | null>(null);
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const [message, setMessage] = useState<"revoked" | "failed" | null>(null);
  const field = useRef<HTMLInputElement>(null);
  const busy = rotate.isPending || revoke.isPending;

  useEffect(() => {
    if (url) field.current?.select();
  }, [url]);

  const make = () => {
    setConfirm(null);
    setMessage(null);
    setCopied(null);
    rotate.mutate(undefined, {
      onSuccess: (r) => setUrl(r.url),
      onError: () => setMessage("failed"),
    });
  };
  const stop = () => {
    setConfirm(null);
    revoke.mutate(undefined, {
      onSuccess: () => {
        setUrl(null);
        setMessage("revoked");
      },
      onError: () => setMessage("failed"),
    });
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url ?? "");
      setCopied("yes");
    } catch {
      field.current?.select();
      setCopied("failed");
    }
  };

  const active = feed.data?.active === true;
  return (
    <Tile as="section" aria-labelledby="feed-h" className="flex flex-col gap-4">
      <h3 id="feed-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("settings.plan.feed_heading")}
      </h3>
      <p className="m-0 max-w-[40rem] text-text-2">{t("settings.plan.feed_body")}</p>
      {feed.isPending ? (
        <Skeleton className="h-12 rounded-block" />
      ) : feed.isError ? (
        <ErrorState
          title={t("settings.plan.feed_failed")}
          message={feed.error instanceof ApiError ? feed.error.message : t("common.error_fallback")}
          onRetry={() => void feed.refetch()}
          retrying={feed.isFetching}
        />
      ) : (
        <>
          {url ? (
            <div className="flex flex-col gap-3">
              <label htmlFor="feed-url" className="text-[0.875rem] text-text-2">
                {t("settings.plan.feed_url_label")}
              </label>
              <input
                id="feed-url"
                ref={field}
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                className="min-h-14 w-full rounded-swatch border border-line-control bg-ground px-[18px] font-mono text-[0.8125rem] text-text"
              />
              <p className="m-0 text-ui-sm text-text-3">{t("settings.plan.feed_once")}</p>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="secondary" icon={<Copy size={16} />} onClick={() => void copy()}>
                  {t("settings.plan.feed_copy")}
                </Button>
                <a
                  href={webcalUrl(url)}
                  className="inline-flex min-h-12 items-center gap-2 rounded-pill border border-line-control px-5 text-text no-underline hover:bg-surface-2 hover:text-text"
                >
                  <CalendarPlus aria-hidden="true" size={16} />
                  {t("settings.plan.feed_subscribe")}
                </a>
                <span role="status" className="text-text-2">
                  {copied === "yes" ? (
                    <span className="inline-flex items-center gap-1">
                      <Check aria-hidden="true" size={16} /> {t("settings.plan.feed_copied")}
                    </span>
                  ) : copied === "failed" ? (
                    t("settings.plan.feed_copy_failed")
                  ) : null}
                </span>
              </div>
            </div>
          ) : (
            <p className="m-0 text-text-2" data-feed={active ? "active" : "off"}>
              {active && feed.data?.created_at
                ? t("settings.plan.feed_active", { date: fmtDate(feed.data.created_at) })
                : t("settings.plan.feed_off")}
            </p>
          )}
          {confirm ? (
            <InlineConfirm
              text={t(confirm === "rotate" ? "settings.plan.feed_confirm_rotate" : "settings.plan.feed_confirm_revoke")}
              yes={t("settings.plan.feed_confirm_yes")}
              no={t("settings.plan.feed_confirm_no")}
              busy={busy}
              onYes={confirm === "rotate" ? make : stop}
              onNo={() => setConfirm(null)}
            />
          ) : (
            <div className="flex flex-wrap gap-3">
              {active || url ? (
                <>
                  <Button variant="secondary" disabled={busy || !online} onClick={() => setConfirm("rotate")}>
                    {t("settings.plan.feed_rotate")}
                  </Button>
                  <Button variant="secondary" disabled={busy || !online} onClick={() => setConfirm("revoke")}>
                    {t("settings.plan.feed_revoke")}
                  </Button>
                </>
              ) : (
                <Button variant="secondary" disabled={busy || !online} onClick={make}>
                  {t("settings.plan.feed_create")}
                </Button>
              )}
            </div>
          )}
          {message ? (
            <p role="status" className="m-0 text-text-2">
              {t(message === "revoked" ? "settings.plan.feed_revoked" : "settings.plan.feed_failed")}
            </p>
          ) : null}
        </>
      )}
    </Tile>
  );
}

function InstallSteps({ t }: { t: T }) {
  return (
    <div className="flex flex-col gap-3" data-reminders="not-installed">
      <p className="m-0 font-semibold text-text">{t("settings.plan.reminders_install_title")}</p>
      <p className="m-0 text-text-2">{t("settings.plan.reminders_install_why")}</p>
      <ol className="m-0 flex flex-col gap-1 pl-5 text-text-2">
        <li>{t("settings.plan.reminders_install_1")}</li>
        <li>{t("settings.plan.reminders_install_2")}</li>
        <li>{t("settings.plan.reminders_install_3")}</li>
      </ol>
    </div>
  );
}

function Reminders() {
  const t = useUiText();
  const online = useOnlineStatus();
  const support = pushSupport();
  const key = usePushKey();
  const configured = key.data?.configured === true;
  const ready = support === "ready" && configured;
  const devices = useDevices(ready);
  const add = useAddDevice();
  const remove = useRemoveDevice();
  const test = useSendTest();
  const [here, setHere] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const permission = permissionState();

  useEffect(() => {
    if (ready) void currentEndpoint().then(setHere);
  }, [ready]);

  const enable = async () => {
    if (!key.data?.public_key) return;
    setWorking(true);
    setNote(null);
    const r = await subscribeThisDevice(key.data.public_key);
    if (!r.ok) {
      setNote(r.reason === "denied" ? "settings.plan.reminders_denied" : "settings.plan.reminders_failed");
      setWorking(false);
      return;
    }
    add.mutate(
      { endpoint: r.subscription.endpoint, keys: r.subscription.keys },
      {
        onSuccess: () => setHere(r.subscription.endpoint),
        onError: () => setNote("settings.plan.reminders_failed"),
        onSettled: () => setWorking(false),
      },
    );
  };
  const disable = async () => {
    setWorking(true);
    setNote(null);
    const mine = devices.data?.subscriptions;
    const ok = await unsubscribeThisDevice();
    setHere(null);
    // The server row of this device: the registration is the only record of which one it is, so a device that
    // cannot tell is removed from the list by hand.
    if (ok && mine && mine.length === 1 && mine[0]) remove.mutate(mine[0].id);
    setWorking(false);
  };
  const sendTest = () => {
    setNote(null);
    test.mutate(undefined, {
      onSuccess: (r) => setNote(r.sent > 0 ? "settings.plan.reminders_test_sent" : "settings.plan.reminders_test_none"),
      onError: () => setNote("settings.plan.reminders_test_failed"),
    });
  };

  const subs = devices.data?.subscriptions ?? [];
  const on = here !== null && subs.length > 0;
  const blocked = permission === "denied";
  return (
    <Tile as="section" aria-labelledby="reminders-h" className="flex flex-col gap-4">
      <h3 id="reminders-h" className="m-0 font-display font-bold text-[1.25rem]">
        {t("settings.plan.reminders_heading")}
      </h3>
      <p className="m-0 max-w-[40rem] text-text-2">{t("settings.plan.reminders_body")}</p>
      {key.isPending ? (
        <Skeleton className="h-12 rounded-block" />
      ) : key.isError ? (
        <ErrorState
          title={t("settings.plan.reminders_failed")}
          message={key.error instanceof ApiError ? key.error.message : t("common.error_fallback")}
          onRetry={() => void key.refetch()}
          retrying={key.isFetching}
        />
      ) : !configured ? (
        <div className="flex flex-col gap-2" data-reminders="server-off">
          <p className="m-0 font-semibold text-text">{t("settings.plan.reminders_server_off_title")}</p>
          <p className="m-0 text-text-2">{t("settings.plan.reminders_server_off")}</p>
        </div>
      ) : support === "unsupported" ? (
        <p className="m-0 text-text-2" data-reminders="unsupported">
          {t("settings.plan.reminders_unsupported")}
        </p>
      ) : support === "not_standalone" ? (
        <InstallSteps t={t} />
      ) : (
        <div className="flex flex-col gap-4" data-reminders={on ? "on" : "off"}>
          <div className="flex flex-wrap items-center gap-3">
            <Chip tone={on ? "done" : "meta"} icon={<Bell size={12} />}>
              {t(on ? "settings.plan.reminders_on" : "settings.plan.reminders_off")}
            </Chip>
          </div>
          {blocked ? <p className="m-0 text-amber">{t("settings.plan.reminders_denied")}</p> : null}
          <div className="flex flex-wrap items-center gap-3">
            {on ? (
              <Button variant="secondary" disabled={working || !online} onClick={() => void disable()}>
                {t(working ? "settings.plan.reminders_working" : "settings.plan.reminders_disable")}
              </Button>
            ) : (
              <Button variant="secondary" disabled={working || !online || blocked} onClick={() => void enable()}>
                {t(working ? "settings.plan.reminders_working" : "settings.plan.reminders_enable")}
              </Button>
            )}
            {subs.length > 0 ? (
              <Button variant="secondary" disabled={test.isPending || !online} onClick={sendTest}>
                {t("settings.plan.reminders_test")}
              </Button>
            ) : null}
            {!online ? <span className="text-text-3">{t("planner.rebalance_offline")}</span> : null}
          </div>
          {note ? (
            <p role="status" className="m-0 text-text-2">
              {t(note)}
            </p>
          ) : null}
          {devices.isPending ? (
            <Skeleton className="h-12 rounded-block" />
          ) : subs.length > 0 ? (
            <div className="flex flex-col gap-2">
              <h4 className="m-0 font-mono text-label text-text-3 uppercase">{t("settings.plan.devices")}</h4>
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {subs.map((d) => {
                  const label = d.ua_label ?? t("settings.plan.device_unknown");
                  return (
                    <li
                      key={d.id}
                      className="flex min-h-12 items-center justify-between gap-3 border-line-soft border-b py-2 last:border-b-0"
                    >
                      <span className="text-text">
                        {label} · {fmtDate(d.created_at)}
                      </span>
                      <Button
                        variant="secondary"
                        aria-label={t("settings.plan.device_remove", { label })}
                        icon={<Trash2 size={16} />}
                        disabled={remove.isPending || !online}
                        onClick={() => remove.mutate(d.id)}
                      />
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Tile>
  );
}

/** Settings → Study plan: the calendar feed and the daily reminders (segment 16). */
export function StudyPlanSettings() {
  const t = useUiText();
  return (
    <section aria-labelledby="study-plan-h" className="flex flex-col gap-5" data-testid="study-plan-settings">
      <h2 id="study-plan-h" className="m-0 font-display font-bold text-[1.5rem]">
        {t("settings.plan.heading")}
      </h2>
      <div className="grid items-start gap-5 laptop:grid-cols-2">
        <CalendarFeed />
        <Reminders />
      </div>
    </section>
  );
}
