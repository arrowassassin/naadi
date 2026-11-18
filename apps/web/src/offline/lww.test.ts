import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { decide, type LwwDecision, type LwwInput } from "./lww";

/** The golden vectors pytest runs against naadi_api/sync/lww.py: the device and the server decide the same. */
const vectors = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../../../../packages/shared/fixtures/lww_vectors.json"), "utf8"),
) as (LwwInput & { name: string; expect: LwwDecision })[];

describe("last-writer-wins golden vectors (shared with the API)", () => {
  it("has vectors for both orders, ties, the clamp and tombstones", () => {
    expect(vectors.length).toBeGreaterThan(10);
  });
  for (const v of vectors) it(v.name, () => expect(decide(v)).toEqual(v.expect));
});
