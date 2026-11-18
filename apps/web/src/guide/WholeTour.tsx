import { useRouter } from "@tanstack/react-router";
import { driver } from "driver.js";
import { useEffect, useRef } from "react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import "driver.js/dist/driver.css";
import "./guide.css";

const stops = [
  { to: "/", flag: "today", topic: "flow", selector: "#today-flow,#today-h" },
  { to: "/learn", flag: "courses", topic: "chapter", selector: "#chapters-h" },
  { to: "/practice", flag: "qbank", topic: "practice", selector: "#practice-h" },
  { to: "/cards", flag: "cards", topic: "cards", selector: "#main section:not([aria-busy]) button" },
  { to: "/notes", flag: "notes", topic: "notebook", selector: "[data-notebooks],#notes-q" },
  { to: "/plan", flag: "planner", topic: "week", selector: "#plan-h" },
  { to: "/pathway", flag: "pathway", topic: "stage", selector: '[data-rail-current="true"]' },
  { to: "/learn/library", flag: "library", topic: "intake", selector: "[data-drop-zone]" },
  { to: "/search", flag: "search", topic: "search", selector: "#main input" },
  {
    to: "/settings/account",
    flag: "assistant",
    topic: "ask",
    selector: '[data-assistant="dock"] button,[data-assistant="tab"]',
  },
  { to: "/settings/account", flag: "", topic: "settings", selector: "[data-settings-nav]" },
] as const;

export function WholeTour({ close, hasPlan }: { close: () => void; hasPlan: boolean }) {
  const router = useRouter();
  const me = useMe();
  const t = useUiText();
  const initial = useRef({ close, hasPlan, features: me.data?.features, t });
  useEffect(() => {
    const { close, features, hasPlan, t } = initial.current;
    const origin = router.state.location.href;
    const enabled = stops.filter((s) => (!s.flag || features?.[s.flag]) && (s.to !== "/" || hasPlan));
    let disposed = false;
    let busy = false;
    let cancelWait = () => {};
    const elements: Array<Element | undefined> = [];
    const asHtml = (text: string) => {
      const span = document.createElement("span");
      span.textContent = text;
      return span.innerHTML;
    };
    const tour = driver({
      animate: false,
      disableActiveInteraction: true,
      showProgress: true,
      stageRadius: 12,
      popoverClass: "guide-step",
      progressText: asHtml(t("guide.position", { n: "{{current}}", total: "{{total}}" })),
      nextBtnText: asHtml(t("guide.next")),
      prevBtnText: asHtml(t("guide.previous")),
      doneBtnText: asHtml(t("guide.end")),
      closeBtnLabel: t("guide.skip"),
      steps: enabled.map((s, i) => ({
        element: () => elements[i] ?? document.querySelector("#main") ?? document.body,
        popover: { title: asHtml(t(`guide.${s.topic}.title`)), description: asHtml(t(`guide.${s.topic}.body`)) },
      })),
      onNextClick: () => {
        const i = tour.getActiveIndex() ?? 0;
        if (i === enabled.length - 1) tour.destroy();
        else void move(i + 1);
      },
      onPrevClick: () => void move((tour.getActiveIndex() ?? 1) - 1),
      onDoneClick: () => tour.destroy(),
      onDestroyed: () => {
        if (!disposed) {
          disposed = true;
          cancelWait();
          close();
          void router.navigate({ href: origin, replace: true });
        }
      },
    });
    async function move(index: number) {
      const stop = enabled[index];
      if (busy || !stop || disposed) return;
      busy = true;
      try {
        await router.navigate({ to: stop.to, replace: true });
        if (disposed) return;
        // Routes and data load separately. Empty accounts still get the explanation for each enabled screen.
        await new Promise<void>((resolve) => {
          const finish = () => {
            observer.disconnect();
            clearTimeout(timer);
            cancelWait = () => {};
            resolve();
          };
          const check = () => {
            const el = [...document.querySelectorAll<HTMLElement>(stop.selector)].find(
              (el) => el.offsetWidth > 2 && el.offsetHeight > 2,
            );
            if (el) {
              elements[index] = el;
              finish();
            }
          };
          const observer = new MutationObserver(check);
          const timer = window.setTimeout(finish, 2500);
          cancelWait = finish;
          observer.observe(document.body, { childList: true, subtree: true, attributes: true });
          check();
        });
        if (!disposed) {
          if (tour.isActive()) tour.moveTo(index);
          else tour.drive(index);
        }
      } catch {
        if (!disposed) {
          tour.destroy();
          close();
        }
      } finally {
        busy = false;
      }
    }
    void move(0);
    return () => {
      disposed = true;
      cancelWait();
      tour.destroy();
    };
  }, [router]);
  return null;
}
