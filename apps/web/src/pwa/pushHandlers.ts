/**
 * Web Push in the service worker (segment 16). Payload (naadi_api/push/sender.py):
 * `{ k: ui_copy key, n: numbers, title, body, url, tag }`: already worded by the server from ui_copy, never note text.
 * A missing or malformed payload still shows a generic notification (browsers require one per push).
 */

/** The shown title when a push carries no readable payload: the brand, not copy. */
const FALLBACK_TITLE = "Naadi";
const FALLBACK_URL = "/plan";

export interface PushPayload {
  title: string;
  body?: string;
  url: string;
  tag?: string;
}

/** Same-origin in-app paths only ("/plan", "/learn/x?y"): anything else (absolute URLs, "//host", scripts) opens the planner. */
export function safeUrl(raw: unknown): string {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return FALLBACK_URL;
  }
  return raw;
}

export function parsePayload(text: string | null): PushPayload {
  if (!text) return { title: FALLBACK_TITLE, url: FALLBACK_URL };
  try {
    const raw: unknown = JSON.parse(text);
    if (typeof raw !== "object" || raw === null) return { title: FALLBACK_TITLE, url: FALLBACK_URL };
    const o = raw as Record<string, unknown>;
    return {
      title: typeof o.title === "string" && o.title ? o.title.slice(0, 120) : FALLBACK_TITLE,
      body: typeof o.body === "string" && o.body ? o.body.slice(0, 300) : undefined,
      url: safeUrl(o.url),
      tag: typeof o.tag === "string" && o.tag ? o.tag.slice(0, 80) : undefined,
    };
  } catch {
    return { title: FALLBACK_TITLE, url: FALLBACK_URL };
  }
}

/** The slice of a ServiceWorkerGlobalScope these handlers use (so tests can fake it). */
export interface WorkerScope {
  addEventListener(type: "push", fn: (e: PushLike) => void): void;
  addEventListener(type: "notificationclick", fn: (e: ClickLike) => void): void;
  registration: { showNotification(title: string, o?: NotificationOptions): Promise<void> };
  clients: {
    matchAll(o: { type: "window"; includeUncontrolled: boolean }): Promise<WindowClientLike[]>;
    openWindow(url: string): Promise<unknown>;
  };
  location: { origin: string };
}
export interface WindowClientLike {
  url: string;
  focus(): Promise<unknown>;
  navigate?(url: string): Promise<unknown>;
}
export interface PushLike {
  data: { text(): string } | null;
  waitUntil(p: Promise<unknown>): void;
}
export interface ClickLike {
  notification: { close(): void; data?: unknown };
  waitUntil(p: Promise<unknown>): void;
}

export function registerPushHandlers(scope: WorkerScope): void {
  scope.addEventListener("push", (event) => {
    const p = parsePayload(event.data ? event.data.text() : null);
    event.waitUntil(
      scope.registration.showNotification(p.title, {
        body: p.body,
        tag: p.tag,
        data: { url: p.url },
        icon: "/icons/icon-192.png",
        badge: "/icons/icon-192.png",
      }),
    );
  });

  scope.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const data = event.notification.data as { url?: unknown } | undefined;
    const url = safeUrl(data?.url);
    event.waitUntil(
      (async () => {
        const all = await scope.clients.matchAll({ type: "window", includeUncontrolled: true });
        const mine = all.find((c) => new URL(c.url).origin === scope.location.origin);
        if (mine) {
          // Focusing needs the user's tap (always there for a real click); a failure must not stop the navigation.
          await mine.focus().catch(() => undefined);
          if (mine.navigate) {
            try {
              await mine.navigate(url);
              return;
            } catch {
              // an uncontrolled window cannot be navigated by the worker: open the link in a window instead
            }
          }
        }
        await scope.clients.openWindow(url);
      })(),
    );
  });
}
