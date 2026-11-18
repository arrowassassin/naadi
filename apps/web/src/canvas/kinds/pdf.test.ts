import { afterEach, describe, expect, it } from "vitest";
import { getCanvasKind, resetCanvasForTests, usableKind } from "../registry";
import { registerBuiltInCanvasKinds } from "./index";

afterEach(() => resetCanvasForTests());

describe("canvas kind pdf (S15)", () => {
  it("is registered with the built-in kinds, behind the library flag, with a strict payload", () => {
    registerBuiltInCanvasKinds();
    const kind = getCanvasKind("pdf");
    expect(kind?.requires_flag).toBe("library");
    expect(kind?.label_key).toBe("canvas.kind.pdf");
    expect(kind?.schema.safeParse({ upload_id: "0b9a7c8e-1f2d-4c3b-9a8e-7d6c5b4a3f21", page: 2 }).success).toBe(true);
    expect(kind?.schema.safeParse({ upload_id: "not-a-uuid", page: 2 }).success).toBe(false);
    expect(usableKind("pdf", { library: true })).toBeDefined();
    expect(usableKind("pdf", {})).toBeUndefined();
  });
});
