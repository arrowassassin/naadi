/**
 * Requests still out when the page goes away (a navigation, a reload, closing the tab) are cancelled by the
 * browser. WebKit then logs "Fetch API cannot load … due to access control checks" for each one as a JavaScript
 * error, even though the code that made the request handles the failure (no `unhandledrejection` fires; Playwright
 * reports the line as a page error, segment 13 review defect 5). Aborting them ourselves on `beforeunload`, before
 * the browser cancels them, ends them as ordinary aborts: nothing is logged.
 *
 * Nothing is lost by aborting: reads are fetched again on the next page, and writes stay in their offline queue
 * (an abort counts as "unreachable") and are sent again, idempotently. If the page stays after all (another
 * `beforeunload` handler asked and the student chose to stay), requests go out again after a moment and the aborted
 * ones are retried like any unreachable request.
 */
let controller: AbortController | null = null;
let installed = false;
let timer = 0;
/** After a `beforeunload` that did not unload the page, requests go out again after this long. */
const STAY_MS = 2_000;

function install(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const fresh = () => {
    controller = null;
  };
  const leave = () => {
    // Abort what is out, and keep aborting what starts now (a refetch, a queue flush on pagehide) until the page
    // is gone. If it stays (a `beforeunload` prompt answered "stay"), requests go out again shortly after; a page
    // restored from the back-forward cache starts fresh at once.
    controller ??= new AbortController();
    controller.abort();
    window.clearTimeout(timer);
    timer = window.setTimeout(fresh, STAY_MS);
  };
  window.addEventListener("beforeunload", leave);
  window.addEventListener("pagehide", leave);
  window.addEventListener("pageshow", fresh);
}

function current(): AbortSignal {
  install();
  controller ??= new AbortController();
  return controller.signal;
}

/** A signal that aborts when the page is being left, or when `signal` aborts. */
export function untilLeave(signal?: AbortSignal): AbortSignal {
  const leave = current();
  if (!signal) return leave;
  if (typeof AbortSignal.any === "function") return AbortSignal.any([signal, leave]);
  // Safari before 17.4: link the two by hand.
  const both = new AbortController();
  const stop = () => both.abort();
  if (signal.aborted || leave.aborted) stop();
  signal.addEventListener("abort", stop, { once: true });
  leave.addEventListener("abort", stop, { once: true });
  return both.signal;
}

/** Test hook. */
export function resetPageLeaveForTests(): void {
  window.clearTimeout(timer);
  controller = null;
}
