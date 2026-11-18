import { describe, expect, it } from "vitest";
import { isNeverCached, isPublicFigure, isShellNavigation, precacheable } from "./rules";

const ORIGIN = "https://naadi.example";
const SB = "https://abc.supabase.co";
const u = (s: string) => new URL(s);

describe("service worker cache rules", () => {
  it("caches public content figures only", () => {
    expect(isPublicFigure(u(`${SB}/storage/v1/object/public/content-public/figures/f1.svg`))).toBe(true);
    expect(isPublicFigure(u(`${SB}/storage/v1/object/public/vault/x.pdf`))).toBe(false);
    expect(isPublicFigure(u(`${SB}/storage/v1/object/sign/content-public/f1.svg?token=abc`))).toBe(false);
    expect(isPublicFigure(u(`${SB}/storage/v1/object/public/content-public/f1.svg?token=abc`))).toBe(false);
  });

  it("never caches the API, private or signed Storage URLs, or YouTube", () => {
    for (const s of [
      `${ORIGIN}/api/offline/week`,
      `${ORIGIN}/api/questions/q1`,
      `${ORIGIN}/api`,
      `${SB}/storage/v1/object/sign/vault/u/doc.pdf?token=t`,
      `${SB}/storage/v1/object/authenticated/uploads/u/a.pdf`,
      `${SB}/storage/v1/object/public/vault/u/a.pdf`,
      "https://www.youtube-nocookie.com/embed/abc",
      "https://i.ytimg.com/vi/abc/hqdefault.jpg",
      "https://rr1.googlevideo.com/videoplayback",
    ])
      expect(isNeverCached(u(s), ORIGIN), s).toBe(true);
    expect(isNeverCached(u(`${SB}/storage/v1/object/public/content-public/f.svg`), ORIGIN)).toBe(false);
    expect(isNeverCached(u(`${ORIGIN}/assets/index-abc.js`), ORIGIN)).toBe(false);
  });

  it("answers page navigations with the shell, never /api or files", () => {
    expect(isShellNavigation(u(`${ORIGIN}/learn/ch01`), ORIGIN)).toBe(true);
    expect(isShellNavigation(u(`${ORIGIN}/`), ORIGIN)).toBe(true);
    expect(isShellNavigation(u(`${ORIGIN}/api/me`), ORIGIN)).toBe(false);
    expect(isShellNavigation(u(`${ORIGIN}/manifest.webmanifest`), ORIGIN)).toBe(false);
    expect(isShellNavigation(u("https://elsewhere.example/learn"), ORIGIN)).toBe(false);
  });

  it("precaches the shell, assets, fonts, icons and the pdf.js worker, never anything under /api", () => {
    for (const p of [
      "index.html",
      "assets/index-a1.js",
      "assets/pdf.worker.min-b2.mjs",
      "assets/inter-c3.woff2",
      "icons/icon-192.png",
      "manifest.webmanifest",
    ])
      expect(precacheable(p), p).toBe(true);
    for (const p of ["api/offline/week", "assets/data.json", "stats.html.map"]) expect(precacheable(p), p).toBe(false);
  });
});

describe("runtime asset cache", () => {
  it("covers the app's own hashed build output only", async () => {
    const { isAppAsset } = await import("./rules");
    expect(isAppAsset(new URL(`${ORIGIN}/assets/PathwayPage-abc.js`), ORIGIN)).toBe(true);
    expect(isAppAsset(new URL(`${ORIGIN}/api/vault/1/url`), ORIGIN)).toBe(false);
    expect(isAppAsset(new URL("https://cdn.example/assets/x.js"), ORIGIN)).toBe(false);
  });
});
