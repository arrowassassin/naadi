import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { CanvasItemView } from "./CanvasView";
import {
  type CanvasKindProps,
  type CanvasRef,
  getCanvasKind,
  openInCanvas,
  registerCanvasKeep,
  registerCanvasKind,
  resetCanvasForTests,
  usableKind,
  useCanvas,
  useCanvasKeep,
} from "./registry";

const me = vi.hoisted(() => ({ features: {} as Record<string, boolean> }));
vi.mock("../auth/api", () => ({ useMe: () => ({ data: { features: me.features } }) }));

const ref = (key: string, over: Partial<CanvasRef> = {}): CanvasRef => ({
  key,
  kind: "note-test",
  origin: "assistant",
  title_key: null,
  title: `Item ${key}`,
  ref: null,
  data: { text: key },
  ...over,
});

function Echo({ item, update }: CanvasKindProps<{ text: string }>) {
  return (
    <button type="button" onClick={() => update({ origin: "student" })}>
      {`echo:${item.data.text}`}
    </button>
  );
}

afterEach(() => {
  resetCanvasForTests();
  me.features = {};
});

describe("canvas registry", () => {
  it("registers kinds; the later registration of the same kind wins; unregister is exact", () => {
    const a = {
      kind: "k",
      schema: z.object({}),
      load: async () => ({ default: () => null }),
      label_key: "a",
      icon: "x",
    };
    const b = { ...a, label_key: "b" };
    const offA = registerCanvasKind(a);
    const offB = registerCanvasKind(b);
    expect(getCanvasKind("k")?.label_key).toBe("b");
    offA(); // A was replaced: its unregister does not remove B
    expect(getCanvasKind("k")?.label_key).toBe("b");
    offB();
    expect(getCanvasKind("k")).toBeUndefined();
  });

  it("a kind behind a flag is usable only while the flag is on", () => {
    const off = registerCanvasKind({
      kind: "pdf-test",
      schema: z.object({}),
      load: async () => ({ default: () => null }),
      label_key: "x",
      icon: "x",
      requires_flag: "library",
    });
    expect(usableKind("pdf-test", {})).toBeUndefined();
    expect(usableKind("pdf-test", { library: true })).toBeDefined();
    off();
  });

  it("opens, replaces by key, focuses, updates and closes items", () => {
    const { result } = renderHook(() => useCanvas());
    act(() => openInCanvas(ref("a")));
    act(() => openInCanvas(ref("b")));
    expect(result.current.items.map((i) => i.key)).toEqual(["a", "b"]);
    expect(result.current.active).toBe("b");
    act(() => openInCanvas(ref("a", { title: "Replaced" })));
    expect(result.current.items.map((i) => i.title)).toEqual(["Replaced", "Item b"]);
    expect(result.current.active).toBe("a");
    act(() => result.current.focus("b"));
    expect(result.current.active).toBe("b");
    act(() => result.current.update("b", { origin: "student", key: "ignored" }));
    expect(result.current.items[1]).toMatchObject({ key: "b", origin: "student" });
    act(() => result.current.close("b"));
    expect(result.current.items.map((i) => i.key)).toEqual(["a"]);
    expect(result.current.active).toBe("a");
    act(() => result.current.close());
    expect(result.current.items).toEqual([]);
    expect(result.current.active).toBeNull();
  });

  it("renders a valid item lazily; invalid data and unknown kinds get the shared error state", async () => {
    const off = registerCanvasKind<{ text: string }>({
      kind: "note-test",
      schema: z.object({ text: z.string() }),
      load: async () => ({ default: Echo }),
      label_key: "canvas.kind.document",
      icon: "file-text",
    });
    act(() => openInCanvas(ref("a")));
    const { unmount } = render(<CanvasItemView item={ref("a")} />);
    expect(await screen.findByRole("button", { name: "echo:a" })).toBeInTheDocument();
    unmount();

    render(<CanvasItemView item={ref("bad", { data: { text: 42 } })} />);
    expect(screen.getByText("This can't be shown")).toBeInTheDocument();

    render(<CanvasItemView item={ref("x", { kind: "nope" })} />);
    expect(screen.getByText("Not available")).toBeInTheDocument();
    off();
  });

  it("Keep exists only while a handler is registered", () => {
    const { result } = renderHook(() => useCanvasKeep());
    expect(result.current).toBeNull();
    const handler = vi.fn(async () => ({ type: "note", id: "n1" }));
    let off: () => void = () => {};
    act(() => {
      off = registerCanvasKeep(handler);
    });
    expect(result.current).toBe(handler);
    act(() => off());
    expect(result.current).toBeNull();
  });
});
