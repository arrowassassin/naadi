import { describe, expect, it } from "vitest";
import { markerModeFor } from "./engine";

describe("markerModeFor", () => {
  it("multiplies over PDF pages and over any surface with opaque blocks; sits under plain text", () => {
    expect(markerModeFor("pdf_page")).toBe("multiply");
    expect(markerModeFor("section")).toBe("under");
    expect(markerModeFor("section", { hasOpaqueBlocks: true })).toBe("multiply");
    expect(markerModeFor("question", { hasOpaqueBlocks: false })).toBe("under");
    expect(markerModeFor("flashcard", { hasOpaqueBlocks: true })).toBe("multiply");
  });
});
