import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { BarChart, LineChart, niceMax } from "./Chart";

const labels = { showTable: "Show as table", hideTable: "Hide table", noData: "No data yet" };
const pct = (v: number) => `${Math.round(v)}%`;
const rows = [
  { system: "Renal", score: 72, prior: 60 },
  { system: "Cardio", score: 55, prior: null },
  { system: "Respiratory", score: 81, prior: 70 },
];
const trend = [
  { date: "Mon", score: 40 },
  { date: "Tue", score: null },
  { date: "Wed", score: 66 },
];

describe("charts", () => {
  it("rounds the scale to a clean number", () => {
    expect(niceMax(0)).toBe(1);
    expect(niceMax(7)).toBe(10);
    expect(niceMax(81)).toBe(100);
    expect(niceMax(120)).toBe(200);
    expect(niceMax(2300)).toBe(2500);
  });

  it("bar chart: one bar per value, a legend for two series, the data as a table", async () => {
    const { container } = render(
      <BarChart
        title="Score by system"
        x="system"
        xLabel="System"
        series={[
          { key: "score", label: "This block" },
          { key: "prior", label: "Last block" },
        ]}
        rows={rows}
        formatValue={pct}
        max={100}
        labels={labels}
      />,
    );
    expect(screen.getByRole("img", { name: "Score by system" })).toBeInTheDocument();
    // null values draw nothing (no invented bar)
    expect(container.querySelectorAll('path[data-series="score"]')).toHaveLength(3);
    expect(container.querySelectorAll('path[data-series="prior"]')).toHaveLength(2);
    // identity is never colour alone: a legend names both series
    expect(screen.getByRole("list")).toHaveTextContent("Last block");
    // the table is always there for assistive tech, visually hidden until toggled
    const table = screen.getByRole("table", { name: "Score by system" });
    expect(table.parentElement).toHaveClass("sr-only");
    expect(within(table).getByRole("rowheader", { name: "Cardio" })).toBeInTheDocument();
    expect(within(table).getAllByText("—")).toHaveLength(1);
    const toggle = screen.getByRole("button", { name: "Show as table" });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Hide table" })).toBeInTheDocument();
    expect(table.parentElement).not.toHaveClass("sr-only");
  });

  it("shows one tooltip with every series on hover", async () => {
    const { container } = render(
      <BarChart
        title="Score by system"
        x="system"
        xLabel="System"
        series={[
          { key: "score", label: "This block" },
          { key: "prior", label: "Last block" },
        ]}
        rows={rows}
        formatValue={pct}
        max={100}
        labels={labels}
      />,
    );
    const hit = container.querySelectorAll('g[data-bar-group="Renal"] rect.fill-transparent')[0];
    if (!hit) throw new Error("no hit target");
    await userEvent.hover(hit);
    const tip = container.querySelector("[data-chart-tooltip]");
    expect(tip).toHaveTextContent("Renal");
    expect(tip).toHaveTextContent("72%");
    expect(tip).toHaveTextContent("60%");
  });

  it("single series: no legend box (the title names it)", () => {
    render(
      <LineChart
        title="Score trend"
        x="date"
        xLabel="Date"
        series={[{ key: "score", label: "Score" }]}
        rows={trend}
        formatValue={pct}
        labels={labels}
      />,
    );
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Score trend" })).toBeInTheDocument();
  });

  it("line chart breaks at a missing value and marks each point", () => {
    const { container } = render(
      <LineChart
        title="Trend"
        x="date"
        xLabel="Date"
        series={[{ key: "score", label: "Score" }]}
        rows={trend}
        formatValue={pct}
        labels={labels}
      />,
    );
    const d = container.querySelector('g[data-series="score"] path')?.getAttribute("d") ?? "";
    expect(d.match(/M/g)).toHaveLength(2);
    expect(container.querySelectorAll('g[data-series="score"] circle')).toHaveLength(2);
  });

  it("says so when there is no number to plot", () => {
    render(
      <BarChart
        title="Empty"
        x="system"
        xLabel="System"
        series={[{ key: "score", label: "Score" }]}
        rows={[{ system: "Renal", score: null }]}
        formatValue={pct}
        labels={labels}
      />,
    );
    expect(screen.getByText("No data yet")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
