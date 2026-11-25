import { describe, expect, it } from "vitest";
import { thumbUrl } from "./thumbUrl";

describe("thumbUrl", () => {
  it("YouTube: the mqdefault still", () => {
    expect(thumbUrl({ youtube_id: "-0CXs9L9O0A", file_url: null })).toBe(
      "https://i.ytimg.com/vi/-0CXs9L9O0A/mqdefault.jpg",
    );
  });
  it("Commons: the thumb path with a standard width", () => {
    expect(
      thumbUrl({
        youtube_id: null,
        file_url: "https://upload.wikimedia.org/wikipedia/commons/a/ab/Acute_leukemia.webm",
      }),
    ).toBe(
      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Acute_leukemia.webm/330px--Acute_leukemia.webm.jpg",
    );
  });
  it("nothing for a foreign host, a malformed path or no ids", () => {
    expect(thumbUrl({ youtube_id: null, file_url: "https://example.com/wikipedia/commons/a/ab/X.webm" })).toBeNull();
    expect(
      thumbUrl({ youtube_id: null, file_url: "https://upload.wikimedia.org/wikipedia/commons/X.webm" }),
    ).toBeNull();
    expect(thumbUrl({ youtube_id: null, file_url: null })).toBeNull();
  });
});
