import { describe, expect, it } from "vitest";
import { coreFiles, precached } from "./precache";

const manifest = {
  "index.html": { file: "assets/main-1.js", css: ["assets/main-1.css"], imports: ["_shared-2.js"] },
  "_shared-2.js": { file: "assets/shared-2.js", assets: ["assets/inter-3.woff2"] },
  "src/learn/ReaderPage.tsx": { file: "assets/ReaderPage-4.js", imports: ["_shared-2.js", "_editor-5.js"] },
  "_editor-5.js": { file: "assets/editor-5.js" },
  "src/admin/pages/index.ts": { file: "assets/admin-6.js", imports: ["_shared-2.js"] },
  "../../node_modules/katex/dist/katex.mjs": { file: "assets/katex-7.js" },
};

describe("precache selection", () => {
  it("is the static closure of the core roots (CSS and fonts included), nothing else", () => {
    const core = coreFiles(manifest, ["index.html", "src/learn/ReaderPage.tsx", /\/katex\/dist\/katex\.mjs$/]);
    expect([...core].sort()).toEqual([
      "assets/ReaderPage-4.js",
      "assets/editor-5.js",
      "assets/inter-3.woff2",
      "assets/katex-7.js",
      "assets/main-1.css",
      "assets/main-1.js",
      "assets/shared-2.js",
    ]);
    expect(core.has("assets/admin-6.js")).toBe(false);
  });

  it("keeps the shell files outside /assets and only the core chunks inside it", () => {
    const core = new Set(["assets/main-1.js"]);
    expect(precached("index.html", core)).toBe(true);
    expect(precached("/icons/icon-192.png", core)).toBe(true);
    expect(precached("assets/main-1.js", core)).toBe(true);
    expect(precached("assets/admin-6.js", core)).toBe(false);
  });
});
