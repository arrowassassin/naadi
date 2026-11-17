import { describe, expect, it } from "vitest";
import type { Citation } from "./protocol";
import { sourcesOf } from "./Thread";

const c = (id: string, n: number): Citation => ({
  id,
  message_id: null,
  title: `Source ${n}`,
  source: "course",
  layer: "course",
  url: null,
  target: null,
  license: null,
  n,
});

describe("sources of a rendered answer", () => {
  it("lists the answer's final sources once, not the tool's copy next to them (review14 r2 #2)", () => {
    const tool = [c("kb:section:ch05-s16@lc_run--a", 1), c("kb:section:ch02-s03@lc_run--a", 2)];
    const final = [c("kb:section:ch05-s16@lc_run--a#final", 1)];
    expect(sourcesOf([...tool, ...final]).map((x) => x.id)).toEqual(["kb:section:ch05-s16@lc_run--a#final"]);
  });

  it("without final sources, one entry per source", () => {
    const list = [c("kb:a@lc_run--1", 1), c("kb:a@lc_run--2", 1), c("kb:b@lc_run--1", 2)];
    expect(sourcesOf(list).map((x) => x.id)).toEqual(["kb:a@lc_run--1", "kb:b@lc_run--1"]);
  });
});
