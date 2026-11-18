import { Button } from "@naadi/ui";
import { driver } from "driver.js";
import { useLayoutEffect, useRef } from "react";
import { useUiText } from "../copy/useUiText";
import { topics } from "./topics";
import "driver.js/dist/driver.css";
import "./guide.css";

function target(selector: string) {
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find(
    (el) => el.offsetWidth > 2 && el.offsetHeight > 2,
  );
}

export default function AppGuide({ close }: { close: () => void }) {
  const t = useUiText();
  const dialog = useRef<HTMLDialogElement>(null);
  const tour = useRef<ReturnType<typeof driver> | null>(null);
  const available = topics.flatMap((topic) => {
    const element = target(topic.selector);
    return element ? [{ ...topic, element }] : [];
  });
  const groups = [...new Set(topics.map((topic) => topic.group))];
  function start(id?: string) {
    dialog.current?.close();
    // Driver accepts HTML for popover copy. Render configured text literally.
    const html = (value: string) => {
      const node = document.createElement("span");
      node.textContent = value;
      return node.innerHTML;
    };
    tour.current = driver({
      animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      disableActiveInteraction: true,
      stagePadding: 5,
      stageRadius: 12,
      popoverClass: "guide-step",
      showProgress: true,
      progressText: html(t("guide.position", { n: "{{current}}", total: "{{total}}" })),
      nextBtnText: html(t("guide.next")),
      prevBtnText: html(t("guide.previous")),
      doneBtnText: html(t("guide.end")),
      closeBtnLabel: t("guide.close"),
      onDestroyed: close,
      steps: available.map(({ id, element }) => ({
        element,
        popover: { title: html(t("guide." + id + ".title")), description: html(t("guide." + id + ".body")) },
      })),
    });
    tour.current.drive(
      Math.max(
        0,
        available.findIndex((topic) => topic.id === id),
      ),
    );
  }
  useLayoutEffect(() => {
    const el = dialog.current;
    const opener = document.activeElement;
    el?.showModal();
    return () => {
      el?.close();
      tour.current?.destroy();
      if (opener instanceof HTMLElement) opener.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog ref={dialog} onCancel={close} aria-label={t("guide.title")} className="guide-index">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2>{t("guide.title")}</h2>
        <Button variant="secondary" onClick={close}>
          {t("guide.close")}
        </Button>
      </header>
      {available.length > 0 && <Button onClick={() => start()}>{t("guide.tour")}</Button>}
      <h3>{t("guide.browse")}</h3>
      {groups.map((group) => (
        <details key={group}>
          <summary>{t("guide.group." + group)}</summary>
          {topics
            .filter((topic) => topic.group === group)
            .map((topic) => (
              <section key={topic.id}>
                <h4>{t("guide." + topic.id + ".title")}</h4>
                <p>{t("guide." + topic.id + ".body")}</p>
                {available.some((entry) => entry.id === topic.id) && (
                  <Button variant="secondary" onClick={() => start(topic.id)}>
                    {t("guide.here")}
                  </Button>
                )}
              </section>
            ))}
        </details>
      ))}
    </dialog>
  );
}
