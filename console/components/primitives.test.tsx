import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { Chip, ariaLabelOf, lookOf } from "./Chip";
import { KPITile, formatValue } from "./KPITile";
import { Meter, toneOf, valueText } from "./Meter";
import { ScoreBreakdown } from "./ScoreBreakdown";

afterEach(cleanup);

async function noViolations(container: HTMLElement): Promise<void> {
  const results = await axe.run(container, {
    rules: { "color-contrast": { enabled: false } }, // jsdom computes no colours
  });
  expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

describe("Chip", () => {
  it("pairs every value with a role and a glyph, and speaks its kind", () => {
    expect(lookOf("status", "blocked")).toMatchObject({ role: "warn", glyph: "■" });
    expect(lookOf("status", "merged")).toMatchObject({ role: "pass", glyph: "✓" });
    expect(lookOf("risk", "risk:governed")).toMatchObject({ role: "fail", glyph: "▮▮▮" });
    expect(lookOf("gate", "skipped")).toMatchObject({ role: "neutral", dashed: true });
    expect(lookOf("severity", "Blocker")).toMatchObject({ role: "fail", solid: true });
    expect(lookOf("verdict", "revision_needed")).toMatchObject({ role: "warn", glyph: "↻" });
    expect(lookOf("label", "factory:ready")).toMatchObject({ glyph: "⌗" });
    expect(lookOf("status", "unknown")).toMatchObject({ role: "neutral", glyph: "" });
    expect(ariaLabelOf("status", "blocked")).toBe("Task state: blocked");
  });

  it("renders as an image with the text value visible and passes axe", async () => {
    const { container } = render(
      <>
        <Chip kind="status" value="blocked" />
        <Chip kind="risk" value="risk:high" disabled />
        <Chip kind="severity" value="Blocker" />
        <Chip kind="gate" value="skipped" />
      </>,
    );
    const blocked = screen.getByRole("img", { name: "Task state: blocked" });
    expect(blocked.textContent).toContain("blocked");
    expect(blocked.getAttribute("data-role")).toBe("warn");
    const risk = screen.getByRole("img", { name: "Risk: risk:high, disabled" });
    expect(risk.getAttribute("data-disabled")).toBe("true");
    await noViolations(container);
  });
});

describe("Meter", () => {
  it("changes tone and glyph together at 80% and at the cap", () => {
    expect(toneOf(46.1, 54)).toMatchObject({ role: "warn", glyph: "▲", percent: 85 });
    expect(toneOf(10, 54)).toMatchObject({ role: "info", glyph: "●" });
    expect(toneOf(54, 54)).toMatchObject({ role: "fail", glyph: "■", percent: 100 });
    expect(toneOf(0, 0)).toMatchObject({ role: "info", percent: 0 });
    expect(valueText(46.1, 54)).toBe("$46.10 of $54.00, 85 percent");
  });

  it("is a meter with a spoken value and passes axe in every state", async () => {
    const { container } = render(
      <>
        <Meter label="Spend today" used={46.1} cap={54} />
        <Meter label="Task budget" used={1} cap={6} state="loading" />
        <Meter label="Task budget" used={1} cap={6} state="error" />
      </>,
    );
    const meter = screen.getByRole("meter", { name: "Spend today" });
    expect(meter.getAttribute("aria-valuetext")).toBe("Spend today: $46.10 of $54.00, 85 percent");
    expect(meter.getAttribute("aria-valuenow")).toBe("46.1");
    expect(meter.textContent).toContain("$46.10 / $54.00");
    expect(meter.textContent).toContain("85%");
    const loading = screen.getAllByRole("meter", { name: "Task budget" })[0];
    expect(loading?.getAttribute("aria-busy")).toBe("true");
    await noViolations(container);
  });
});

describe("KPITile", () => {
  it("formats values and shows a dash when not readable", () => {
    expect(formatValue(31.7, "%", 1)).toBe("31.7%");
    expect(formatValue(2.0, "$", 2)).toBe("$2.00");
    expect(formatValue(null, "%", 1)).toBe("—");
    expect(formatValue(3, "min", 0)).toBe("3 min");
  });

  it("is a group with a full label, in every status, and passes axe", async () => {
    const { container } = render(
      <>
        <KPITile label="Merge share" value={31.7} unit="%" status="on" target="≥ 30%" trend={[1, 2, 3, 4, 5, 6, 7]} />
        <KPITile label="Merge rate" value={61} unit="%" status="off" target="≥ 70%" note="3 of 5 open" />
        <KPITile label="Unattended" value={null} status="nodata" target="≥ 80%" />
        <KPITile label="Audit" value={null} status="loading" target="≤ 5%" />
        <KPITile label="Cost" value={null} status="error" target="≤ 2× median" />
      </>,
    );
    const tile = screen.getByRole("group", { name: "Merge share: 31.7%, on target, target ≥ 30%" });
    expect(tile.getAttribute("data-status")).toBe("on");
    expect(screen.getByRole("group", { name: /Unattended: —, no data yet/ })).toBeTruthy();
    expect(screen.getByRole("group", { name: /Cost: —, not readable/ })).toBeTruthy();
    await noViolations(container);
  });
});

describe("ScoreBreakdown", () => {
  it("shows base × factors, the equation, the outcome chip and an override", async () => {
    const { container } = render(
      <ScoreBreakdown
        instrument="risk"
        version={1}
        base={70}
        baseLabel="Tier B"
        factors={[
          { name: "size", rule: "over 200 lines: 1.3", value: 1.3 },
          { name: "modules", rule: "3 modules: 1.2", value: 1.2 },
          { name: "deps", rule: "no new dependency: 1", value: 1 },
        ]}
        score={109.2}
        equation="70 x 1.3 x 1.2 x 1 = 109.2"
        outcome="risk:high"
        conclusion="109.2 is 50 or more: risk:high, plan review"
        override={{ by: "mlima", reason: "touches billing" }}
      />,
    );
    expect(container.textContent).toContain("70 x 1.3 x 1.2 x 1 = 109.2");
    expect(container.textContent).toContain("Overridden by");
    expect(container.textContent).toContain("@mlima");
    expect(screen.getByRole("img", { name: "Risk: risk:high" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Risk score" })).toBeTruthy();
    await noViolations(container);
  });

  it("reads tier U as unscoped with score 0", async () => {
    const { container } = render(
      <ScoreBreakdown
        instrument="review"
        version={1}
        tier="U"
        base={0}
        baseLabel="Tier U"
        factors={[]}
        score={0}
        equation=""
        outcome="accepted"
        conclusion="no findings"
      />,
    );
    expect(container.textContent).not.toContain("Scope names no paths");
    cleanup();
    const unscoped = render(
      <ScoreBreakdown
        instrument="risk"
        version={1}
        tier="U"
        base={0}
        baseLabel="Tier U"
        factors={[]}
        score={0}
        equation=""
        outcome="risk:low"
        conclusion="unclassifiable"
      />,
    );
    expect(unscoped.container.textContent).toContain("Scope names no paths. Score 0.");
    await noViolations(unscoped.container);
  });
});
