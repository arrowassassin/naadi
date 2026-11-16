import { Button, ErrorState, OfflineBanner } from "@naadi/ui";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Clock, MailCheck } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { callbackUrl, redirectOrigin } from "../auth/redirect";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { Wordmark } from "../shell/Logo";

export const RESEND_COOLDOWN_S = 60;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Phase =
  | { name: "idle" }
  | { name: "sending" }
  | { name: "sent"; email: string; sentAt: number }
  | { name: "error"; message: string };

function sendErrorMessage(err: { status?: number; code?: string; message: string }): string {
  if (err.status === 429 || err.code === "over_email_send_rate_limit" || err.code === "over_request_rate_limit") {
    return "Too many sign-in emails were requested. Wait a few minutes, then try again.";
  }
  if (err.code === "email_address_invalid" || err.code === "validation_failed") {
    return "That email address was not accepted. Check it and try again.";
  }
  if (err.code === "signup_disabled" || err.code === "otp_disabled") {
    return "Sign-up with email is turned off for this app. Ask the admin for an invite.";
  }
  if (err.status === 0 || /fetch|network/i.test(err.message)) {
    return "The sign-in service could not be reached. Check your connection and try again.";
  }
  return "The sign-in email could not be sent. Try again in a moment.";
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

export interface SignInPageProps {
  next: string;
  notice?: "expired" | "signed-out" | null;
}

export function SignInPage({ next, notice }: SignInPageProps) {
  const auth = useAuth();
  const online = useOnlineStatus();
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [googleBusy, setGoogleBusy] = useState(false);
  const emailId = useId();
  const hintId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const sentHeading = useRef<HTMLHeadingElement>(null);
  const now = useNow(phase.name === "sent");

  const client: SupabaseClient | null =
    auth.status === "signed-out" || auth.status === "signed-in" ? auth.client : null;
  const config = auth.status === "signed-out" || auth.status === "signed-in" ? auth.config : null;
  const redirectTo = callbackUrl(redirectOrigin(config?.app_base_url), next);
  // Editable strings from app_config.ui_copy (served with /api/public-config); never empty (see useUiText).
  const copy = useUiText();

  useEffect(() => {
    if (phase.name === "sent") sentHeading.current?.focus();
  }, [phase.name]);

  async function send(address: string): Promise<void> {
    if (!client) return;
    setPhase({ name: "sending" });
    const { error } = await client.auth.signInWithOtp({
      email: address,
      options: { emailRedirectTo: redirectTo, shouldCreateUser: true },
    });
    if (error) {
      setPhase({ name: "error", message: sendErrorMessage(error) });
      return;
    }
    setPhase({ name: "sent", email: address, sentAt: Date.now() });
  }

  function onSubmit(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    const address = email.trim();
    if (!EMAIL_RE.test(address)) {
      setFieldError("Enter an email address like name@example.com.");
      inputRef.current?.focus();
      return;
    }
    setFieldError(null);
    void send(address);
  }

  async function google(): Promise<void> {
    if (!client) return;
    setGoogleBusy(true);
    const { error } = await client.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo, queryParams: { prompt: "select_account" } },
    });
    if (error) {
      setGoogleBusy(false);
      setPhase({ name: "error", message: "Google sign-in could not start. Try again, or use an email link." });
    }
  }

  const unavailable = auth.status === "unavailable";
  const sending = phase.name === "sending";
  const cooldownLeft =
    phase.name === "sent" ? Math.max(0, RESEND_COOLDOWN_S - Math.floor((now - phase.sentAt) / 1000)) : 0;

  return (
    <main
      id="main"
      tabIndex={-1}
      className="min-h-dvh bg-ground pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] outline-none"
    >
      <div className="mx-auto flex max-w-(--content-max) flex-col gap-8 px-(--gutter) pt-6 pb-12 tablet:gap-10">
        <header className="flex items-center justify-between">
          <Wordmark className="text-[1.375rem]" size={32} />
        </header>

        <h1 className="m-0 font-display font-extrabold text-[clamp(3.5rem,2rem+8vw,9.25rem)] leading-[0.88] tracking-[-0.05em]">
          Welcome
          <br />
          back<span className="text-lime">.</span>
        </h1>

        <div className="flex flex-wrap items-stretch gap-5">
          <section
            aria-labelledby="signin-h"
            className="flex min-w-0 flex-[2_1_520px] flex-col gap-7 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-9"
          >
            <h2 id="signin-h" className="m-0 font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {copy("signin.heading")}
            </h2>

            {!online ? <OfflineBanner message={copy("signin.offline")} /> : null}

            {notice === "expired" && phase.name === "idle" ? (
              <div role="status" className="rounded-block border border-amber/60 bg-surface-2 p-4 text-text">
                <p className="m-0 font-semibold">{copy("signin.link_expired_title")}</p>
                <p className="m-0 text-text-2">{copy("signin.link_expired_body")}</p>
              </div>
            ) : null}
            {notice === "signed-out" && phase.name === "idle" ? (
              <p role="status" className="m-0 text-text-2">
                {copy("signin.signed_out")}
              </p>
            ) : null}

            {unavailable ? (
              <ErrorState
                title={copy("signin.unavailable_title")}
                message={auth.error.message}
                requestId={auth.error.requestId}
                onRetry={() => window.location.reload()}
                retryLabel="Reload"
              />
            ) : null}

            {phase.name === "sent" ? (
              <div className="flex flex-col gap-5" data-state="sent">
                <div className="flex items-start gap-3">
                  <MailCheck aria-hidden="true" size={28} className="mt-1 shrink-0 text-text" />
                  <div className="flex min-w-0 flex-col gap-1">
                    <h3
                      ref={sentHeading}
                      tabIndex={-1}
                      className="m-0 font-display font-bold text-[1.75rem] leading-tight outline-none"
                    >
                      Check your email
                    </h3>
                    <p className="m-0 break-words text-text-2" role="status">
                      We sent a sign-in link to <span className="font-semibold text-text">{phase.email}</span>. Open it
                      on this device to continue.
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    variant="secondary"
                    disabled={cooldownLeft > 0 || !online}
                    onClick={() => void send(phase.email)}
                    icon={cooldownLeft > 0 ? <Clock size={16} /> : undefined}
                  >
                    {cooldownLeft > 0 ? `Resend in ${cooldownLeft} s` : "Resend the link"}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setPhase({ name: "idle" });
                      queueMicrotask(() => inputRef.current?.focus());
                    }}
                  >
                    Use a different email
                  </Button>
                </div>
              </div>
            ) : (
              <>
                {config?.google_oauth_enabled ? (
                  <>
                    <Button
                      variant="secondary"
                      size="cta"
                      className="w-full"
                      disabled={!client || !online || googleBusy || sending}
                      onClick={() => void google()}
                      icon={<GoogleG />}
                    >
                      {copy(googleBusy ? "signin.google_opening" : "signin.google")}
                    </Button>
                    <div className="flex items-center gap-3 text-text-3" aria-hidden="true">
                      <span className="h-px flex-1 bg-line" />
                      <span className="font-mono text-label-sm uppercase">or</span>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                  </>
                ) : null}

                <form noValidate onSubmit={onSubmit} className="flex flex-col gap-4" aria-busy={sending}>
                  <label htmlFor={emailId} className="flex flex-col gap-2 text-[0.875rem] text-text-2">
                    Email
                    <input
                      ref={inputRef}
                      id={emailId}
                      name="email"
                      type="email"
                      inputMode="email"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck={false}
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      aria-invalid={fieldError ? true : undefined}
                      aria-describedby={hintId}
                      disabled={sending}
                      className="min-h-14 rounded-swatch border border-line-control bg-ground px-[18px] text-[1.0625rem] text-text placeholder:text-text-3 aria-invalid:border-amber"
                      placeholder="name@example.com"
                    />
                  </label>
                  <p id={hintId} className={fieldError ? "m-0 text-amber" : "m-0 text-[0.8125rem] text-text-3"}>
                    {fieldError ?? copy("signin.email_hint")}
                  </p>
                  {phase.name === "error" ? (
                    <ErrorState title={copy("signin.send_failed_title")} message={phase.message} />
                  ) : null}
                  <Button
                    type="submit"
                    variant="primary"
                    size="cta"
                    className="w-full"
                    disabled={!client || !online || sending}
                  >
                    {copy(sending ? "signin.sending" : "signin.submit")}
                  </Button>
                </form>
              </>
            )}
          </section>

          <aside
            aria-label="About Naadi"
            className="flex min-w-0 flex-[1_1_320px] flex-col justify-between gap-6 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-9"
          >
            <span className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {copy("signin.tagline")}
            </span>
            <p className="m-0 font-display font-bold text-[1.75rem] leading-[1.1] tracking-[-0.02em]">
              {copy("signin.pitch")}
            </p>
            <p className="m-0 text-text-2">{copy("signin.privacy")}</p>
          </aside>
        </div>
      </div>
    </main>
  );
}

function GoogleG() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"
      />
    </svg>
  );
}
