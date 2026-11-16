import { Button, ErrorState } from "@naadi/ui";
import { useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { type CallbackResult, completeSignInOnce } from "../auth/callback";
import { useAuth } from "../auth/session";
import { Wordmark } from "../shell/Logo";

/** /auth/callback: finishes a magic-link or Google sign-in, then returns to the page the user came from. */
export function CallbackPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [result, setResult] = useState<CallbackResult | null>(null);
  const startedFor = useRef<string | null>(null);

  useEffect(() => {
    if (auth.status !== "signed-out" && auth.status !== "signed-in") return;
    const url = new URL(window.location.href);
    if (startedFor.current === url.href) return;
    startedFor.current = url.href;
    void completeSignInOnce(auth.client, url).then((r) => {
      // Never leave codes or tokens in the address bar or the history.
      window.history.replaceState(window.history.state, "", "/auth/callback");
      if (r.ok) {
        void navigate({ href: r.next, replace: true });
      } else if (r.kind === "expired") {
        void navigate({ to: "/sign-in", search: { error: "expired", redirect: r.next }, replace: true });
      } else {
        setResult(r);
      }
    });
  }, [auth, navigate]);

  const failed = result && !result.ok ? result : null;
  return (
    <main id="main" tabIndex={-1} className="min-h-dvh bg-ground outline-none">
      <div className="mx-auto flex max-w-[40rem] flex-col gap-8 px-(--gutter) pt-6 pb-12">
        <Wordmark className="text-[1.375rem]" size={32} />
        {auth.status === "unavailable" ? (
          <ErrorState
            title="Sign-in could not be completed"
            message={auth.error.message}
            requestId={auth.error.requestId}
            onRetry={() => window.location.reload()}
            retryLabel="Reload"
          />
        ) : failed ? (
          <section aria-labelledby="cb-h" className="flex flex-col gap-5" data-state="error">
            <h1 id="cb-h" className="m-0 font-display text-headline">
              Sign-in did not finish
            </h1>
            <ErrorState title="This link did not work" message={failed.message} />
            <div>
              <Button
                variant="primary"
                size="cta"
                onClick={() => void navigate({ to: "/sign-in", search: { redirect: failed.next } })}
              >
                Back to sign in
              </Button>
            </div>
          </section>
        ) : (
          <section aria-labelledby="cb-h" aria-busy="true" className="flex flex-col gap-4" data-state="processing">
            <h1 id="cb-h" className="m-0 font-display text-headline">
              Signing you in…
            </h1>
            <p role="status" className="m-0 flex items-center gap-3 text-text-2">
              <Loader2 aria-hidden="true" size={20} className="animate-spin" />
              Checking your sign-in link.
            </p>
          </section>
        )}
      </div>
    </main>
  );
}
