import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContentRenderer } from "./ContentRenderer";
import { adoptSvg, diagramStyleCount, escapeMermaidLabels, releaseDiagramStyles } from "./mermaidHost";

describe("math (KaTeX, MathML only)", () => {
  it("renders the formula as MathML with no inline style anywhere", async () => {
    const { container } = render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={{ type: "doc", content: [{ type: "math", attrs: { latex: "\\text{AG} = Na^+ - (Cl^- + HCO_3^-)" } }] }}
      />,
    );
    await waitFor(() => expect(container.querySelector("math")).not.toBeNull());
    const math = container.querySelector("math") as Element;
    expect(math.getAttribute("display")).toBe("block");
    expect(math.textContent).toContain("Na");
    expect(math.querySelector("msup")).not.toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    expect(container.querySelector("annotation")?.textContent).toBe("\\text{AG} = Na^+ - (Cl^- + HCO_3^-)");
  });

  it("an invalid formula is reported in words, not dropped", async () => {
    render(
      <ContentRenderer
        sectionId="s1"
        mode="prod"
        doc={{ type: "doc", content: [{ type: "math", attrs: { latex: "\\frac{" } }] }}
      />,
    );
    expect(await screen.findByText("This formula could not be displayed.")).toBeInTheDocument();
  });
});

describe("mermaid labels", () => {
  it("keeps < and > in labels as entity codes and leaves arrows alone", () => {
    const code =
      "flowchart TD\n  A[Metabolic acidosis] --> B{Gap > 12?}\n  B -->|yes > no| C[High gap]\n  C ==> D((a < b))";
    expect(escapeMermaidLabels(code)).toBe(
      "flowchart TD\n  A[Metabolic acidosis] --> B{Gap #gt; 12?}\n  B -->|yes #gt; no| C[High gap]\n  C ==> D((a #lt; b))",
    );
  });
});

const MERMAID_LIKE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" id="mmd-1-abc" viewBox="0 0 100 50" style="max-width: 100px;">
<style>#mmd-1-abc .node rect{fill:#1b1d22;stroke:#9c9ea6}</style>
<g class="node"><rect width="80" height="30" style="fill: #222"/><text x="10" y="20">Gap &gt; 12?</text></g>
<script>alert(1)</script><foreignObject><div>x</div></foreignObject><a href="javascript:alert(2)"><text>link</text></a>
</svg>`;

describe("adoptSvg (sanitise, no inline style in the document)", () => {
  it("sanitises, keeps label text exactly, moves styles to CSSOM and the shared sheet, releases them", () => {
    const host = document.createElement("div");
    document.body.append(host);
    adoptSvg(host, MERMAID_LIKE_SVG, "mmd-1-abc");
    const svg = host.querySelector("svg") as SVGSVGElement;
    expect(svg).not.toBeNull();
    expect(host.querySelector("script, foreignObject, a, style")).toBeNull();
    expect(host.querySelector("text")?.textContent).toBe("Gap > 12?");
    // no style attribute was ever parsed into the document; the values were applied through element.style
    expect(svg.getAttribute("data-naadi-style")).toBeNull();
    expect(svg.style.getPropertyValue("max-width")).toBe("100px");
    expect((host.querySelector("rect") as SVGRectElement).style.getPropertyValue("fill")).toMatch(
      /^(#222|rgb\(34, 34, 34\))$/,
    );
    if ("adoptedStyleSheets" in document) {
      expect(diagramStyleCount()).toBe(1);
      releaseDiagramStyles("mmd-1-abc");
      expect(diagramStyleCount()).toBe(0);
    }
    host.remove();
  });

  it("refuses output that is not an SVG", () => {
    const host = document.createElement("div");
    expect(() => adoptSvg(host, "<div>nope</div>", "x")).toThrow();
  });
});
