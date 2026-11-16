import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { usePenActive, usePenSurface } from "./chrome";

function Surface({ active }: { active: boolean }) {
  usePenSurface(active);
  return null;
}

function Reader() {
  return <output>{usePenActive() ? "pen" : "idle"}</output>;
}

describe("pen-surface registration", () => {
  it("is ref-counted across surfaces and cleans up on unmount", () => {
    const view = render(
      <>
        <Surface active={false} />
        <Surface active={false} />
        <Reader />
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("idle");
    expect(document.documentElement.hasAttribute("data-pen-active")).toBe(false);

    act(() =>
      view.rerender(
        <>
          <Surface active />
          <Surface active />
          <Reader />
        </>,
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("pen");
    expect(document.documentElement.hasAttribute("data-pen-active")).toBe(true);

    act(() =>
      view.rerender(
        <>
          <Surface active={false} />
          <Surface active />
          <Reader />
        </>,
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("pen"); // one surface still has the pen

    act(() => view.unmount());
    expect(document.documentElement.hasAttribute("data-pen-active")).toBe(false);
  });
});
