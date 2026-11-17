import type { NavItem } from "@naadi/shared";
import { cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, Outlet, useRouter, useRouterState } from "@tanstack/react-router";
import { Search, UserRound, X } from "lucide-react";
import { type RefObject, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { AssistantHost, AssistantTabButton } from "../assistant/AssistantHost";
import { useAssistantAvailable } from "../assistant/ask";
import { useMe, useNav } from "../auth/api";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { useActiveExam } from "../exam/api";
import { ExamActiveBanner, ExamActiveGate, examGates, onExamRoute } from "../exam/ExamActive";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { SyncIndicator } from "../offline/SyncStatus";
import { UpdatePrompt } from "../offline/UpdatePrompt";
import { useSearchCopy } from "../search/copy";
import { useSearchShortcut, useSuppressSearchShortcut } from "../search/shortcut";
import { setShellBottomSlot, usePenActive } from "./chrome";
import { NavIcon } from "./icons";
import { LogoMark } from "./Logo";

const ACCOUNT_PATH = "/settings/account";

function isCurrent(path: string, pathname: string): boolean {
  if (path === "/") return pathname === "/";
  return pathname === path || pathname.startsWith(`${path}/`);
}

/**
 * Publishes the chrome's size as --shell-top / --shell-bottom on :root (contract with S12a): px, 0px when
 * the element is hidden at this breakpoint or moved out of the way for the pen.
 */
function useInsetVar(ref: RefObject<HTMLElement | null>, name: string, collapsed = false): void {
  useLayoutEffect(() => {
    const el = ref.current;
    const root = document.documentElement;
    if (!el) return;
    const apply = () =>
      root.style.setProperty(name, collapsed ? "0px" : `${Math.round(el.getBoundingClientRect().height)}px`);
    apply();
    // Written on the next frame: a synchronous write from the observer can resize what it observes (the assistant's
    // Dock and sheet live in the bottom bar) and WebKit then reports "ResizeObserver loop completed".
    let frame = 0;
    const later = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(apply);
    };
    const ro = new ResizeObserver(later);
    ro.observe(el);
    window.addEventListener("resize", apply);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      window.removeEventListener("resize", apply);
      root.style.setProperty(name, "0px");
    };
  }, [ref, name, collapsed]);
}

function initialOf(name: string | null | undefined, email: string | null | undefined): string {
  const src = (name ?? "").trim() || (email ?? "").trim();
  const ch = src ? Array.from(src)[0] : "";
  return ch ? ch.toLocaleUpperCase() : "";
}

function Avatar({ initial, current, label }: { initial: string; current: boolean; label: string }) {
  return (
    <Link
      to={ACCOUNT_PATH}
      aria-label={label}
      aria-current={current ? "page" : undefined}
      className={cx(
        "flex size-11 shrink-0 items-center justify-center rounded-pill border font-semibold text-[0.875rem] no-underline",
        current
          ? "border-paper bg-paper text-ground"
          : "border-violet-line bg-violet-ground text-violet-text hover:text-violet-text",
      )}
    >
      {initial ? <span aria-hidden="true">{initial}</span> : <UserRound aria-hidden="true" size={18} />}
    </Link>
  );
}

/** The round search control from the Nav board: opens Search (Ctrl/Cmd+K does the same from anywhere). */
function SearchButton({ current, label }: { current: boolean; label: string }) {
  return (
    <Link
      to="/search"
      aria-label={label}
      aria-keyshortcuts="Control+K Meta+K"
      aria-current={current ? "page" : undefined}
      className={cx(
        "flex size-11 shrink-0 items-center justify-center rounded-pill no-underline",
        current
          ? "bg-paper text-ground hover:text-ground"
          : "bg-surface-2 text-text hover:bg-line-strong hover:text-text",
      )}
    >
      <Search aria-hidden="true" size={18} strokeWidth={2} />
    </Link>
  );
}

function PillItem({ item, current }: { item: NavItem; current: boolean }) {
  return (
    <li>
      <Link
        to={item.path}
        aria-current={current ? "page" : undefined}
        className={cx(
          "flex min-h-11 items-center gap-2 rounded-pill text-[0.875rem] whitespace-nowrap no-underline",
          "transition-colors duration-(--duration-control) ease-out",
          current
            ? "bg-paper px-[18px] font-semibold text-ground hover:text-ground"
            : "px-4 text-text-2 hover:bg-surface-2 hover:text-text",
        )}
      >
        {item.label}
      </Link>
    </li>
  );
}

function TabItem({ to, label, icon, current }: { to: string; label: string; icon: string; current: boolean }) {
  return (
    <li className="flex min-w-0 flex-1">
      <Link
        to={to}
        aria-current={current ? "page" : undefined}
        className={cx(
          "flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-pill px-1 py-1.5 text-[0.6875rem] leading-tight no-underline",
          current ? "bg-paper font-semibold text-ground hover:text-ground" : "text-text-2 hover:text-text",
        )}
      >
        <NavIcon name={icon} size={18} />
        <span className="max-w-full text-center">{label}</span>
      </Link>
    </li>
  );
}

/** The phone tab bar shows at most this many tabs (More included): eight labels do not fit a 390 px pill. */
const MAX_TABS = 5;

interface TabSpec {
  id: string;
  to: string;
  label: string;
  icon: string;
}

/** A tab that opens the More sheet; current when the open page is one of the tabs the sheet holds. */
function MoreTab({ label, current, onOpen }: { label: string; current: boolean; onOpen: () => void }) {
  return (
    <li className="flex min-w-0 flex-1">
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-current={current ? "page" : undefined}
        className={cx(
          "flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-pill border-0 px-1 py-1.5 text-[0.6875rem] leading-tight",
          current ? "bg-paper font-semibold text-ground" : "bg-transparent text-text-2 hover:text-text",
        )}
      >
        <NavIcon name="ellipsis" size={18} />
        <span className="max-w-full text-center">{label}</span>
      </button>
    </li>
  );
}

/** Everything that did not fit the tab bar, as large links in a sheet. */
function MoreSheet({
  open,
  tabs,
  pathname,
  onClose,
}: {
  open: boolean;
  tabs: TabSpec[];
  pathname: string;
  onClose: () => void;
}) {
  const t = useUiText();
  const ref = useRef<HTMLDialogElement>(null);
  const ids = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${ids}-h`}
      onClose={onClose}
      data-more-sheet=""
      className="m-auto mb-3 w-[min(100%-24px,28rem)] rounded-tile-sm border border-line-soft bg-surface-1 p-0 text-text backdrop:bg-ground/80"
    >
      <div className="flex flex-col gap-3 p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 id={`${ids}-h`} className="m-0 font-display font-bold text-[1.375rem]">
            {t("shell.more_title")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("shell.more_close")}
            className="flex size-11 items-center justify-center rounded-full border border-line-control bg-transparent text-text"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {tabs.map((tab) => {
            const current = isCurrent(tab.to, pathname);
            return (
              <li key={tab.id}>
                <Link
                  to={tab.to}
                  onClick={onClose}
                  aria-current={current ? "page" : undefined}
                  className={cx(
                    "flex min-h-12 items-center gap-3 rounded-pill px-4 text-[1rem] no-underline",
                    current ? "bg-paper font-semibold text-ground hover:text-ground" : "text-text hover:bg-surface-2",
                  )}
                >
                  <NavIcon name={tab.icon} size={20} />
                  {tab.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </dialog>
  );
}

export function AppShell() {
  const auth = useAuth();
  const me = useMe();
  const nav = useNav();
  const online = useOnlineStatus();
  const router = useRouter();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const topRef = useRef<HTMLElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [slotEl, slotRef] = useState<HTMLDivElement | null>(null);
  // Tablet and up: the assistant's Dock bar (S14) lives in the same bottom bar, so --shell-bottom covers it.
  const [dockEl, dockRef] = useState<HTMLDivElement | null>(null);
  // The bottom slot exists only where the tab bar is shown (phone, < 640 px).
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639.98px)");
    const apply = () => setShellBottomSlot(mq.matches ? slotEl : null);
    apply();
    mq.addEventListener("change", apply);
    return () => {
      mq.removeEventListener("change", apply);
      setShellBottomSlot(null);
    };
  }, [slotEl]);
  const penActive = usePenActive();
  useInsetVar(topRef, "--shell-top");
  useInsetVar(bottomRef, "--shell-bottom", penActive);

  // Only routes that exist in this build are linked, whatever the server lists: no dead navigation.
  const routes = router.routesByPath as Record<string, unknown>;
  const items = (nav.data?.items ?? []).filter((i) => i.path in routes);
  const email = auth.status === "signed-in" ? auth.session.user.email : null;
  const initial = initialOf(me.data?.profile.display_name, me.data?.email ?? email);
  const onAccount = isCurrent("/settings", pathname);
  // Search shows once the server turns the feature on and this build has the screen.
  // A timed/exam session running on the server (S09): Search and practice stay closed until it is over, also
  // after "Leave", and Ctrl/Cmd+K does nothing (the server refuses tutor practice with 409 exam_active).
  const active = useActiveExam().data?.active ?? null;
  useSuppressSearchShortcut(active !== null);
  const searchOn = me.data?.features.search === true && "/search" in routes && active === null;
  const searchCopy = useSearchCopy();
  const t = useUiText();
  useSearchShortcut(searchOn);
  const navLoading = nav.isPending && !nav.data;
  const [moreOpen, setMoreOpen] = useState(false);
  const allTabs: TabSpec[] = [
    ...items.map((i) => ({ id: i.id, to: i.path, label: i.label, icon: i.icon })),
    ...(searchOn ? [{ id: "search", to: "/search", label: searchCopy("search.tab"), icon: "search" }] : []),
    { id: "account", to: ACCOUNT_PATH, label: t("shell.account_tab"), icon: "user-round" },
  ];
  // Up to five tabs fit; beyond that, four tabs and a More tab that opens the rest.
  // The assistant's round centre button takes a slot of its own while it can answer.
  const slots = MAX_TABS - (useAssistantAvailable() ? 1 : 0);
  const overflow = allTabs.length > slots;
  const shownTabs = overflow ? allTabs.slice(0, slots - 1) : allTabs;
  const moreTabs = overflow ? allTabs.slice(slots - 1) : [];
  const offline = !online || (auth.status === "signed-in" && auth.offline);

  return (
    <div className="min-h-dvh bg-ground" data-shell="">
      <header
        ref={topRef}
        // Its own compositing layer: without it WebKit repaints the whole page on every scroll frame under the
        // sticky bar (25-30% lower fps on iPad), on every page that has the shell.
        className="sticky top-0 z-20 hidden px-4 pt-[max(16px,env(safe-area-inset-top))] pb-2 will-change-transform tablet:block"
      >
        <nav
          aria-label={t("shell.nav_main")}
          aria-busy={navLoading || undefined}
          className="mx-auto flex w-fit max-w-full items-center gap-1 rounded-[28px] border border-line bg-bar/85 p-1.5 backdrop-blur-[18px]"
        >
          <Link
            to="/"
            aria-label={t("shell.home_label")}
            className="flex min-h-11 shrink-0 items-center gap-2 rounded-pill pr-3.5 pl-2.5 font-bold font-display text-[1.1875rem] text-text no-underline hover:text-text"
          >
            <LogoMark />
            <span aria-hidden="true">Naadi</span>
          </Link>
          <ul className="m-0 flex min-w-0 list-none items-center gap-1 overflow-x-auto p-0">
            {navLoading
              ? [0, 1, 2].map((i) => (
                  <li key={i}>
                    <Skeleton className="h-11 w-20 rounded-pill" />
                  </li>
                ))
              : items.map((item) => <PillItem key={item.id} item={item} current={isCurrent(item.path, pathname)} />)}
          </ul>
          {searchOn ? (
            <SearchButton current={isCurrent("/search", pathname)} label={searchCopy("search.title")} />
          ) : null}
          <Avatar initial={initial} current={onAccount} label={t("shell.account_label")} />
        </nav>
      </header>
      <div aria-hidden="true" className="h-[env(safe-area-inset-top)] tablet:hidden" />

      {/* Sync state (segment 18): offline with what will sync, syncing, saved, or what needs attention. */}
      <div className="mx-auto w-full max-w-(--content-max) px-(--gutter) pt-3 empty:hidden has-[>[hidden]]:hidden">
        <SyncIndicator offline={offline} />
      </div>
      <div className="mx-auto w-full max-w-(--content-max) px-(--gutter) pt-3 empty:hidden">
        <UpdatePrompt hidden={active !== null} />
      </div>

      {/* The window scrolls; nothing here sets touch-action, transform or filter on main or its ancestors
          (pen surfaces depend on it). Children are centred at 1320 px unless they opt out with data-bleed. */}
      <main
        id="main"
        tabIndex={-1}
        data-shell-content=""
        className={cx(
          "w-full pt-6 pb-[calc(var(--shell-bottom,0px)+32px)] outline-none",
          "[&>*:not([data-bleed])]:mx-auto [&>*:not([data-bleed])]:max-w-(--content-max) [&>*:not([data-bleed])]:px-(--gutter)",
        )}
      >
        {nav.isError && !nav.data ? (
          <ErrorState
            className="mb-6"
            title={t("shell.nav_failed_title")}
            message={nav.error.message}
            requestId={"requestId" in nav.error ? (nav.error.requestId as string | null) : null}
            onRetry={() => void nav.refetch()}
            retrying={nav.isFetching}
          />
        ) : null}
        {active && !onExamRoute(pathname, active) ? <ExamActiveBanner active={active} /> : null}
        {examGates(pathname, active) && active ? <ExamActiveGate active={active} /> : <Outlet />}
      </main>

      <div
        ref={bottomRef}
        data-collapsed={penActive || undefined}
        data-shell-bottom=""
        className={cx(
          // Hidden from tablet up unless the assistant's Dock is in it (assistant/assistant.css).
          "fixed inset-x-0 bottom-0 z-20 px-3 pt-2 pb-[max(12px,env(safe-area-inset-bottom))] tablet:hidden",
          "transition-transform duration-(--duration-sheet) ease-out",
          // While the pen is down the tab bar slides away; keyboard focus inside brings it back.
          penActive && "translate-y-[calc(100%+8px)] focus-within:translate-y-0",
        )}
      >
        {/* One surface on phone: a page's own bottom row (the reader's page bar, via a portal into the slot)
            sits above the tabs inside it; the slot is collapsed while empty. */}
        <div className="flex flex-col gap-1 rounded-[28px] border border-line-strong bg-bar/95 p-1.5 backdrop-blur-[18px] tablet:hidden">
          <div ref={slotRef} data-shell-bottom-slot="" className="empty:hidden" />
          <nav aria-label={t("shell.nav_tabs")} aria-busy={navLoading || undefined}>
            <ul className="m-0 flex list-none items-center gap-1 p-0">
              {navLoading ? (
                <li className="flex flex-1">
                  <Skeleton className="h-12 w-full rounded-pill" />
                </li>
              ) : (
                (() => {
                  const slots = shownTabs.length + (overflow ? 1 : 0);
                  const mid = Math.floor((slots + 1) / 2) - 1;
                  const nodes = shownTabs.map((tab) => (
                    <TabItem
                      key={tab.id}
                      to={tab.to}
                      label={tab.label}
                      icon={tab.icon}
                      current={isCurrent(tab.to, pathname)}
                    />
                  ));
                  if (overflow) {
                    nodes.push(
                      <MoreTab
                        key="more"
                        label={t("shell.more_tab")}
                        current={moreTabs.some((tab) => isCurrent(tab.to, pathname))}
                        onOpen={() => setMoreOpen(true)}
                      />,
                    );
                  }
                  // The assistant's centre button (Phone board), present only while it can answer.
                  nodes.splice(mid + 1, 0, <AssistantTabButton key="assistant" />);
                  return nodes;
                })()
              )}
            </ul>
          </nav>
        </div>
        <div ref={dockRef} data-assistant-dock="" className="hidden tablet:block" />
      </div>
      <AssistantHost dockSlot={dockEl} onExamRoute={active !== null && onExamRoute(pathname, active)} />
      <MoreSheet open={moreOpen} tabs={moreTabs} pathname={pathname} onClose={() => setMoreOpen(false)} />
    </div>
  );
}
