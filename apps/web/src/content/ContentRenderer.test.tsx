import { readFileSync } from "node:fs";
import path from "node:path";
import type { MediaOut } from "@naadi/shared";
import { CONTENT_MARK_TYPES, CONTENT_NODE_TYPES, ContentDocSchema } from "@naadi/shared/content";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContentRenderer } from "./ContentRenderer";
import { sanitizeToReact } from "./SafeHtml";
import { RENDERED_MARK_TYPES, RENDERED_NODE_TYPES } from "./types";

const t = (text: string, ...marks: Array<string | { type: string; attrs?: Record<string, unknown> }>) => ({
  type: "text",
  text,
  ...(marks.length ? { marks: marks.map((m) => (typeof m === "string" ? { type: m } : m)) } : {}),
});
const p = (...c: unknown[]) => ({ type: "paragraph", content: c });
const doc = (...c: unknown[]) => ({ type: "doc", content: c });

const FIG: MediaOut = {
  id: "ch01-fig01",
  kind: "svg",
  url: "https://ref.supabase.co/storage/v1/object/public/content-public/figures/ch01-fig01.svg",
  title: null,
  caption: "Figure 1. Classic pedigree patterns.",
  attribution: "Naadi original (CC BY 4.0)",
  width: 760,
  height: 420,
  youtube_id: null,
  duration_s: null,
  embeddable: null,
  source: { id: "naadi-original-v1", name: "Naadi", license: "CC-BY-4.0", url: null },
};
const VID: MediaOut = {
  ...FIG,
  id: "v1",
  kind: "video_youtube",
  url: null,
  youtube_id: "abc_DEF-123",
  title: "Acid–base",
  duration_s: 1090,
};
const PDF: MediaOut = {
  ...FIG,
  id: "p1",
  kind: "pdf",
  url: "https://ref.supabase.co/sign/x.pdf?token=t",
  title: "Lab values",
};

describe("node coverage", () => {
  it("renders exactly the node and mark types of the shared content schema", () => {
    expect([...RENDERED_NODE_TYPES].sort()).toEqual([...CONTENT_NODE_TYPES].sort());
    expect([...RENDERED_MARK_TYPES].sort()).toEqual([...CONTENT_MARK_TYPES].sort());
  });
});

describe("ContentRenderer", () => {
  it("renders paragraphs, headings (normalised under the section title), lists and marks", () => {
    const { container } = render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={doc(
          { type: "heading", attrs: { level: 3 }, content: [t("Top")] },
          { type: "heading", attrs: { level: 4 }, content: [t("Sub")] },
          p(t("bold", "bold"), t(" "), t("it", "italic"), t("H"), t("2", "subscript"), t("O"), t("2+", "superscript")),
          p(t("a"), { type: "hardBreak" }, t("b")),
          { type: "bulletList", content: [{ type: "listItem", content: [p(t("one"))] }] },
          {
            type: "orderedList",
            attrs: { start: 3, type: "a" },
            content: [{ type: "listItem", content: [p(t("three"))] }],
          },
        )}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Top" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Sub" })).toBeInTheDocument();
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("it");
    expect(container.querySelector("sub")?.textContent).toBe("2");
    expect(container.querySelector("sup")?.textContent).toBe("2+");
    expect(container.querySelector("br")).not.toBeNull();
    expect(container.querySelector("ul li")?.textContent).toBe("one");
    const ol = container.querySelector("ol");
    expect(ol?.getAttribute("start")).toBe("3");
    expect(ol?.getAttribute("type")).toBe("a");
    // anchors for reading positions, one per top-level block
    expect(container.querySelectorAll("[data-content-root] > [data-block-index]")).toHaveLength(6);
  });

  it("highlights carry their meaning as words, not colour alone", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={doc(
          p(
            t("anion gap", { type: "highlight", attrs: { color: "lime", meaning: "key_fact" } }),
            t("Winter", { type: "highlight", attrs: { color: "violet", meaning: "formula" } }),
            t("oops", { type: "highlight", attrs: { color: "amber", meaning: "mistake" } }),
          ),
        )}
      />,
    );
    const marks = document.querySelectorAll("mark");
    expect([...marks].map((m) => m.getAttribute("data-kind"))).toEqual(["key-fact", "formula", "mistake"]);
    expect(marks[0]?.textContent).toBe("Key fact: anion gap");
    expect(marks[1]?.textContent).toBe("Formula: Winter");
    expect(marks[2]?.textContent).toBe("My mistake: oops");
  });

  it("renders tables in a focusable scroll region with header cells, spans, alignment and the high-yield word", () => {
    const cell = (type: string, text: string, attrs: Record<string, unknown> = {}) => ({
      type,
      attrs: { colspan: 1, rowspan: 1, colwidth: null, align: null, ...attrs },
      content: [p(t(text))],
    });
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={doc({
          type: "table",
          attrs: { highYield: true },
          content: [
            { type: "tableRow", content: [cell("tableHeader", "Type"), cell("tableHeader", "K+")] },
            { type: "tableRow", content: [cell("tableCell", "Distal", { colspan: 2, align: "center" })] },
          ],
        })}
      />,
    );
    const region = screen.getByRole("region", { name: "High-yield table" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(screen.getByText("High-yield table", { selector: "span" })).toBeInTheDocument();
    expect(within(region).getAllByRole("columnheader")).toHaveLength(2);
    const td = within(region).getByRole("cell");
    expect(td).toHaveAttribute("colspan", "2");
    expect(td.className).toContain("text-center");
  });

  it("callouts have a distinct icon and label word per variant", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={doc(
          { type: "callout", attrs: { variant: "pearl" }, content: [p(t("p"))] },
          { type: "callout", attrs: { variant: "trap" }, content: [p(t("t"))] },
          {
            type: "callout",
            attrs: { variant: "bridge" },
            content: [{ type: "heading", attrs: { level: 3 }, content: [t("From NEET PG")] }, p(t("b"))],
          },
        )}
      />,
    );
    for (const name of ["Pearl", "Trap", "Bridge"]) {
      const box = screen.getByRole("complementary", { name });
      expect(within(box).getByText(name)).toBeInTheDocument();
      expect(box.querySelector("svg")).not.toBeNull();
    }
  });

  it("figures come from media_assets: Storage URL, intrinsic size, lazy, caption as alt, attribution, zoom", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        media={{ [FIG.id]: FIG }}
        doc={doc({ type: "figure", attrs: { mediaId: FIG.id } })}
      />,
    );
    const img = screen.getByRole("img", { name: FIG.caption ?? "" });
    expect(img).toHaveAttribute("src", FIG.url);
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("width", "760");
    expect(img).toHaveAttribute("height", "420");
    expect(screen.getByText("Source: Naadi original (CC BY 4.0)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Enlarge figure/ }));
    expect(screen.getByRole("button", { name: "Close figure" })).toBeInTheDocument();
  });

  it("a figure without a visible media row says so instead of disappearing", () => {
    render(<ContentRenderer sectionId="s1" mode="prod" doc={doc({ type: "figure", attrs: { mediaId: "gone" } })} />);
    expect(screen.getByText("This figure is not available right now.")).toBeInTheDocument();
  });

  it("unavailable-media wording comes from app_config.ui_copy when it is set", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        copy={(key) => (key === "content.figure_missing" ? "Figure on its way back." : null)}
        doc={doc({ type: "figure", attrs: { mediaId: "gone" } })}
      />,
    );
    expect(screen.getByText("Figure on its way back.")).toBeInTheDocument();
  });

  it("YouTube is a click-to-load facade, then the privacy-enhanced IFrame with origin and referrer policy", () => {
    const { container } = render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        media={{ v1: VID }}
        doc={doc({ type: "video", attrs: { mediaId: "v1", startS: 30, endS: null } })}
      />,
    );
    expect(container.querySelector("iframe")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Play video: Acid–base/ }));
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    const src = new URL(frame?.getAttribute("src") ?? "");
    expect(src.origin).toBe("https://www.youtube-nocookie.com");
    expect(src.pathname).toBe("/embed/abc_DEF-123");
    expect(src.searchParams.get("origin")).toBe(window.location.origin);
    expect(src.searchParams.get("start")).toBe("30");
    expect(frame?.getAttribute("referrerpolicy")).toBe("strict-origin-when-cross-origin");
  });

  it("pdf nodes are real links to the resolved URL and page", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        media={{ p1: PDF }}
        doc={doc({ type: "pdf", attrs: { mediaId: "p1", page: 4 } })}
      />,
    );
    const a = screen.getByRole("link", { name: /Lab values/ });
    expect(a).toHaveAttribute("href", `${PDF.url}#page=4`);
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("question embeds show stem and options only, sanitised", () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        questions={{
          q1: {
            id: "q1",
            stem_html: '<p>A 24-year-old <b>man</b><img src=x onerror="alert(1)"><script>alert(2)</script></p>',
            options: [
              { label: "A", html: "Alpha" },
              { label: "B", html: '<a href="javascript:alert(3)">Beta</a>' },
            ],
          },
        }}
        doc={doc({ type: "questionEmbed", attrs: { questionId: "q1" } })}
      />,
    );
    const box = screen.getByRole("region", { name: "Practice question" });
    expect(box.querySelector("b")?.textContent).toBe("man");
    expect(box.querySelector("img, script, a")).toBeNull();
    expect(box.textContent).toContain("Beta");
  });

  it("unknown nodes: a visible error card in dev, skipped (text kept) and reported in prod", () => {
    const unknown = doc(p(t("before")), { type: "sparkle", content: [p(t("inner text"))] }, p(t("x", "glow")));
    const onDev = vi.fn();
    const dev = render(<ContentRenderer sectionId="s9" mode="dev" doc={unknown} onUnknown={onDev} />);
    expect(screen.getByText(/Unknown content node “sparkle”/)).toBeInTheDocument();
    expect(dev.container.querySelector('[data-unknown-mark="glow"]')).not.toBeNull();
    expect(onDev).toHaveBeenCalledWith({ kind: "node", type: "sparkle", path: "s9 › block 1" });
    dev.unmount();

    const onProd = vi.fn();
    render(<ContentRenderer sectionId="s9" mode="prod" doc={unknown} onUnknown={onProd} />);
    expect(screen.queryByText(/Unknown content/)).toBeNull();
    expect(screen.getByText("inner text")).toBeInTheDocument();
    expect(onProd).toHaveBeenCalledTimes(2);
    expect(onProd.mock.calls.map((c) => c[0].type)).toEqual(["sparkle", "glow"]);
  });

  it("a body that is not a doc renders nothing in prod and an error card in dev", () => {
    const { container } = render(<ContentRenderer sectionId="s1" mode="prod" doc={{ nope: 1 }} />);
    expect(container.textContent).toBe("");
    render(<ContentRenderer sectionId="s1" mode="dev" doc={null} />);
    expect(screen.getByText(/not a doc/)).toBeInTheDocument();
  });
});

describe("every seeded section renders with no unknown node", () => {
  const file = path.resolve(__dirname, "../../../../data/generated/section_bodies.json");
  const seed = JSON.parse(readFileSync(file, "utf8")) as {
    sections: Record<string, { body: unknown; figureIds: string[] }>;
  };
  const entries = Object.entries(seed.sections);

  it("has all 259 sections and each body passes the strict shared schema", () => {
    expect(entries).toHaveLength(259);
    for (const [, s] of entries) expect(ContentDocSchema.safeParse(s.body).success).toBe(true);
  });

  it("renders each section with figures resolved and no unknown content", () => {
    const onUnknown = vi.fn();
    let figures = 0;
    for (const [id, s] of entries) {
      const media = Object.fromEntries(s.figureIds.map((f) => [f, { ...FIG, id: f }]));
      const { container, unmount } = render(
        <ContentRenderer sectionId={id} mode="prod" media={media} doc={s.body} onUnknown={onUnknown} />,
      );
      figures += container.querySelectorAll("[data-node=figure]").length;
      expect(container.querySelector("[data-node=media-unavailable]")).toBeNull();
      unmount();
    }
    expect(onUnknown).not.toHaveBeenCalled();
    expect(figures).toBe(95);
  }, 60_000);
});

describe("sanitizeToReact", () => {
  it("keeps formatting, drops attributes, scripts, links and styles", () => {
    const { container } = render(
      <div>
        {sanitizeToReact(
          '<p style="color:red" onclick="x()">Hi <i>there</i></p><style>p{}</style><table><tr><td colspan="2">c</td></tr></table>',
        )}
      </div>,
    );
    const para = container.querySelector("p");
    expect(para?.getAttribute("style")).toBeNull();
    expect(para?.getAttribute("onclick")).toBeNull();
    expect(container.querySelector("i")?.textContent).toBe("there");
    expect(container.querySelector("style")).toBeNull();
    expect(container.querySelector("td")?.getAttribute("colspan")).toBe("2");
  });
});
