import { useEffect, useState } from "react";

/**
 * Device storage for the offline week (HANDOFF §5.2): Safari evicts data that is not persisted, so on the first
 * launch from the Home Screen we ask for `navigator.storage.persist()`; Settings shows whether it is persisted and
 * how much is used (`navigator.storage.estimate()`).
 */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const nav = navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true || window.matchMedia?.("(display-mode: standalone)").matches === true;
}

export async function isPersisted(): Promise<boolean | null> {
  try {
    return typeof navigator.storage?.persisted === "function" ? await navigator.storage.persisted() : null;
  } catch {
    return null;
  }
}

export async function requestPersist(): Promise<boolean | null> {
  try {
    return typeof navigator.storage?.persist === "function" ? await navigator.storage.persist() : null;
  } catch {
    return null;
  }
}

export interface StorageUse {
  usage: number | null;
  quota: number | null;
}

export async function storageUse(): Promise<StorageUse> {
  try {
    if (typeof navigator.storage?.estimate !== "function") return { usage: null, quota: null };
    const e = await navigator.storage.estimate();
    return { usage: e.usage ?? null, quota: e.quota ?? null };
  } catch {
    return { usage: null, quota: null };
  }
}

const ASKED = "naadi.offline.persist_asked.v1";

/** Once per device, on a launch from the Home Screen: ask the browser to keep this origin's data. */
export async function persistOnFirstStandaloneLaunch(): Promise<void> {
  if (!isStandalone()) return;
  try {
    if (localStorage.getItem(ASKED)) return;
    localStorage.setItem(ASKED, String(Date.now()));
  } catch {
    // no localStorage: asking again on a later launch is harmless
  }
  await requestPersist();
}

/** Bytes as the student reads them ("12 MB", "1.4 GB"). Units are symbols, not copy. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10} ${units[i]}`;
}

export interface StorageState extends StorageUse {
  persisted: boolean | null;
  refresh: () => void;
  persist: () => Promise<void>;
}

export function useStorageState(): StorageState {
  const [state, setState] = useState<StorageUse & { persisted: boolean | null }>({
    usage: null,
    quota: null,
    persisted: null,
  });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    void tick;
    void Promise.all([storageUse(), isPersisted()]).then(([use, persisted]) => {
      if (live) setState({ ...use, persisted });
    });
    return () => {
      live = false;
    };
  }, [tick]);
  return {
    ...state,
    refresh: () => setTick((n) => n + 1),
    persist: async () => {
      await requestPersist();
      setTick((n) => n + 1);
    },
  };
}

// ── Install hint ──

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: BeforeInstallPromptEvent | null = null;
const installListeners = new Set<() => void>();
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as BeforeInstallPromptEvent;
    for (const l of installListeners) l();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    for (const l of installListeners) l();
  });
}

export type InstallMode = "installed" | "prompt" | "ios" | "none";

function isIosSafari(): boolean {
  const ua = navigator.userAgent;
  const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return ios && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
}

/** How this device can install the app: already installed, the browser's prompt, Safari's Share menu, or not at all. */
export function useInstallMode(): { mode: InstallMode; install: () => Promise<void> } {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    installListeners.add(l);
    return () => {
      installListeners.delete(l);
    };
  }, []);
  const mode: InstallMode = isStandalone() ? "installed" : deferred ? "prompt" : isIosSafari() ? "ios" : "none";
  return {
    mode,
    install: async () => {
      const d = deferred;
      if (!d) return;
      await d.prompt();
      await d.userChoice;
      deferred = null;
      force((n) => n + 1);
    },
  };
}
