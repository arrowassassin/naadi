import { describe, expect, it } from "vitest";
import { skippedFiles, skippedSource } from "./precache";

describe("precache selection", () => {
  it("leaves out Shiki grammars and unused themes, keeps everything else", () => {
    expect(
      skippedSource("../../node_modules/.pnpm/@shikijs+langs@3.1.0/node_modules/@shikijs/langs/dist/cpp.mjs"),
    ).toBe(true);
    expect(skippedSource("node_modules/@shikijs/themes/dist/nord.mjs")).toBe(true);
    expect(skippedSource("node_modules/@shikijs/themes/dist/github-dark.mjs")).toBe(false);
    expect(skippedSource("src/learn/ReaderPage.tsx")).toBe(false);
    expect(skippedSource("node_modules/mermaid/dist/mermaid.core.mjs")).toBe(false);
    expect(
      skippedFiles({
        a: { file: "assets/cpp-1.js", src: "node_modules/@shikijs/langs/dist/cpp.mjs" },
        b: { file: "assets/ReaderPage-2.js", src: "src/learn/ReaderPage.tsx" },
      }),
    ).toEqual(new Set(["assets/cpp-1.js"]));
  });
});
