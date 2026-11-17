import type { ImportReport } from "@naadi/shared";
import { uiText } from "@naadi/shared";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../auth/apiError";
import { errorKey, filenameOf } from "./api";
import { ImportReportView } from "./ImportDialog";

const t = (key: string, vars?: Record<string, string>) => uiText(null, key, vars);
const report: ImportReport = {
  notes: 3,
  cards_added: 5,
  cards_existing: 2,
  cards_skipped: 0,
  decks: [{ deck: "Biology / Cells", added: 5, existing: 2 }],
  images_added: 1,
  images_skipped: 2,
  audio_skipped: 4,
  media_manifest: "unreadable",
};

describe("anki api helpers", () => {
  it("maps error codes to copy keys", () => {
    expect(errorKey(new ApiError("x", 422, "anki_not_a_package", null))).toBe("anki.error.anki_not_a_package");
    expect(errorKey(new ApiError("x", 429, "rate_limited", null))).toBe("anki.error.rate_limited");
    expect(errorKey(new ApiError("x", 413, null, null))).toBe("anki.error.anki_too_large");
    expect(errorKey(new ApiError("x", null, null, null))).toBe("anki.error.anki_unavailable");
    expect(errorKey(new Error("boom"))).toBe("anki.error.unknown");
  });
  it("reads the download name from the response header", () => {
    expect(filenameOf(`attachment; filename="naadi-bio.apkg"; filename*=UTF-8''naadi-bio.apkg`, "x")).toBe(
      "naadi-bio.apkg",
    );
    expect(filenameOf(`attachment; filename="a.apkg"`, "x")).toBe("a.apkg");
    expect(filenameOf(null, "fallback.apkg")).toBe("fallback.apkg");
  });
});

describe("import report", () => {
  it("lists every count, the decks and the unreadable-media warning in words", () => {
    render(<ImportReportView report={report} t={t} />);
    expect(screen.getByText("5 cards added")).toBeInTheDocument();
    expect(screen.getByText(/2 already in your library/)).toBeInTheDocument();
    expect(screen.getByText(/2 pictures skipped/)).toBeInTheDocument();
    expect(screen.getByText(/4 sounds and videos skipped/)).toBeInTheDocument();
    expect(screen.getByText("Biology / Cells: 5 added, 2 already there")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("came in without pictures");
  });
  it("omits zero lines and the warning when the manifest was readable", () => {
    render(
      <ImportReportView report={{ ...report, cards_existing: 0, audio_skipped: 0, media_manifest: "ok" }} t={t} />,
    );
    expect(screen.queryByText(/already in your library/)).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });
});
