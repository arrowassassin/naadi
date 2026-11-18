import { afterEach, describe, expect, it } from "vitest";
import { ownsKeys, registerShortcutTarget, type ShortcutAction } from "./shortcuts";

function mount(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.body;
}

/** jsdom has no layout: give an element the scroll size a real browser would measure. */
function overflowing(el: HTMLElement) {
  Object.defineProperty(el, "scrollHeight", { value: 400, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: 100, configurable: true });
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("ownsKeys", () => {
  it("leaves the page itself and plain buttons to the ink", () => {
    mount('<button id="b">Pen</button>');
    expect(ownsKeys(document.body)).toBe(false);
    expect(ownsKeys(document.getElementById("b"))).toBe(false);
    expect(ownsKeys(null)).toBe(false);
  });

  it("gives editable fields their own keys", () => {
    mount(
      '<input id="t" /><input id="c" type="checkbox" /><textarea id="a"></textarea><div id="e" contenteditable="true"></div>',
    );
    expect(ownsKeys(document.getElementById("t"))).toBe(true);
    expect(ownsKeys(document.getElementById("c"))).toBe(false);
    expect(ownsKeys(document.getElementById("a"))).toBe(true);
    const e = document.getElementById("e") as HTMLElement;
    Object.defineProperty(e, "isContentEditable", { value: true });
    expect(ownsKeys(e)).toBe(true);
  });

  it("gives dialogs and media players their own keys", () => {
    mount(
      '<dialog open><button id="d">Cancel</button></dialog><div role="alertdialog"><button id="r">OK</button></div><video id="v" controls></video>',
    );
    expect(ownsKeys(document.getElementById("d"))).toBe(true);
    expect(ownsKeys(document.getElementById("r"))).toBe(true);
    expect(ownsKeys(document.getElementById("v"))).toBe(true);
  });

  it("gives a focused scroll region its keys, but not one that does not overflow", () => {
    mount(
      '<div id="s" tabindex="0" style="overflow-y: auto"></div><div id="n" tabindex="0" style="overflow-y: auto"></div>',
    );
    const s = document.getElementById("s") as HTMLElement;
    overflowing(s);
    expect(ownsKeys(s)).toBe(true);
    expect(ownsKeys(document.getElementById("n"))).toBe(false);
  });
});

describe("registerShortcutTarget", () => {
  it("sends letters to the surface, and nothing while a dialog has focus", () => {
    const got: ShortcutAction[] = [];
    const remove = registerShortcutTarget({
      handleShortcut: (a) => {
        got.push(a);
        return true;
      },
    });
    mount('<dialog open><button id="d">Cancel</button></dialog>');
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    document.getElementById("d")?.dispatchEvent(new KeyboardEvent("keydown", { key: "e", bubbles: true }));
    remove();
    expect(got).toEqual(["marker"]);
  });
});
