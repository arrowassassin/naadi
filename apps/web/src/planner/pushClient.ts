/**
 * The browser side of Web Push for Settings → Study plan (segment 16): can this device get reminders, and
 * subscribe / unsubscribe it. No UI strings here; the component words every state from ui_copy.
 *
 * Reminders need all three: a browser with `PushManager`, the app running installed ("standalone"; iPad and
 * iPhone deliver pushes only to Home Screen apps), and the server's VAPID keys (`GET /api/push/key`, which are
 * only provided once Naadi is reachable at a public address).
 */

export type PushSupport = "ready" | "unsupported" | "not_standalone";

interface StandaloneNavigator extends Navigator {
  standalone?: boolean;
}

export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const mq = typeof window.matchMedia === "function" ? window.matchMedia("(display-mode: standalone)").matches : false;
  return mq || (navigator as StandaloneNavigator).standalone === true;
}

export function pushSupport(): PushSupport {
  if (typeof window === "undefined" || !("PushManager" in window) || !("serviceWorker" in navigator)) {
    return "unsupported";
  }
  if (typeof Notification === "undefined") return "unsupported";
  return isStandalone() ? "ready" : "not_standalone";
}

/** base64url (the VAPID public key as the server sends it) to the bytes `subscribe` wants. */
export function keyBytes(b64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const bin = atob((b64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export type SubscribeResult =
  | { ok: true; subscription: PushSubscriptionJSON & { endpoint: string; keys: { p256dh: string; auth: string } } }
  | { ok: false; reason: "denied" | "failed" };

/** Ask for permission (from a tap), then subscribe this browser with the server's application key. */
export async function subscribeThisDevice(publicKey: string): Promise<SubscribeResult> {
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return { ok: false, reason: "denied" };
    const reg = await navigator.serviceWorker.ready;
    const sub =
      (await reg.pushManager.getSubscription()) ??
      (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }));
    const json = sub.toJSON();
    if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return { ok: false, reason: "failed" };
    return { ok: true, subscription: json as never };
  } catch {
    return { ok: false, reason: "failed" };
  }
}

/** The subscription this browser holds right now, if any (its endpoint says which server row is this device). */
export async function currentEndpoint(): Promise<string | null> {
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    return sub?.endpoint ?? null;
  } catch {
    return null;
  }
}

export async function unsubscribeThisDevice(): Promise<boolean> {
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    return sub ? await sub.unsubscribe() : true;
  } catch {
    return false;
  }
}

export function permissionState(): NotificationPermission | "unsupported" {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

/** https → webcal (Calendar apps open `webcal://` links); other schemes are left alone. */
export function webcalUrl(url: string): string {
  return url.replace(/^https?:\/\//, "webcal://");
}
