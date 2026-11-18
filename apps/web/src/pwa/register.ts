/**
 * Service worker registration (production builds only) and the update flow (segment 18): a new version waits until
 * the student chooses "Reload" in "New version ready · Reload" (never forced, so an exam is never reloaded under
 * them). `apply()` asks the waiting worker to take over and reloads once it controls the page.
 */
type Listener = (apply: () => void) => void;

let waiting: ServiceWorker | null = null;
const listeners = new Set<Listener>();

function announce(): void {
  const w = waiting;
  if (!w) return;
  const apply = () => {
    navigator.serviceWorker.addEventListener("controllerchange", () => window.location.reload(), { once: true });
    w.postMessage({ type: "SKIP_WAITING" });
  };
  for (const l of listeners) l(apply);
}

function track(reg: ServiceWorkerRegistration): void {
  // An update found while a version already controls the page is a new version (the first install is not).
  const consider = (w: ServiceWorker | null) => {
    if (w && navigator.serviceWorker.controller) {
      waiting = w;
      announce();
    }
  };
  consider(reg.waiting);
  reg.addEventListener("updatefound", () => {
    const next = reg.installing;
    next?.addEventListener("statechange", () => {
      if (next.state === "installed") consider(next);
    });
  });
}

export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then(track)
      // The script URL passes the Trusted Types default policy (same origin only). A failed registration is logged
      // for support (no push, no offline shell without a worker), never swallowed.
      .catch((err: unknown) => {
        console.warn("Service worker registration failed", err instanceof Error ? err.name : "unknown");
      });
  });
}

/** Called with `apply` when a new version is installed and waiting. Returns an unsubscribe. */
export function onUpdateReady(cb: Listener): () => void {
  listeners.add(cb);
  if (waiting) announce();
  return () => {
    listeners.delete(cb);
  };
}
