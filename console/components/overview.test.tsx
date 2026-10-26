import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HEADLINE_TILES, targetText, tileStatus, type MetricView } from "@/lib/overview";

import { formatIndicator, labelOf, thresholdText } from "./IndicatorsPanel";
import { total } from "./NavBadge";
import { StuckFlag } from "./StuckFlag";

afterEach(cleanup);

function metric(name: string, value: number | null, numerator?: number, denominator?: number): MetricView {
  return { metric: name, value, numerator: numerator ?? null, denominator: denominator ?? null, target: null, thresholds: null };
}

describe("headline tiles", () => {
  it("judges each tile against the PRD target and reads the sample size", () => {
    const items = [
      metric("factory_merge_share", 33.3, 2, 6),
      metric("merge_rate", 61.0, 11, 18),
      metric("unattended_rate", 80.0, 4, 5),
      metric("audit_disagreement_low", 0.0, 0, 3),
      metric("cost_per_merged_pr", 2.1),
      metric("cost_per_merged_pr_median_30d", 1.2),
      metric("over_budget_count", 1),
    ];
    const [share, rate, unattended, audit, cost] = HEADLINE_TILES;
    expect(tileStatus(share, items)).toMatchObject({ status: "on", note: "2 of 6" });
    expect(tileStatus(rate, items)).toMatchObject({ status: "off", note: "11 of 18" });
    expect(tileStatus(unattended, items)).toMatchObject({ status: "on" });
    expect(tileStatus(audit, items)).toMatchObject({ status: "on" });
    expect(tileStatus(cost, items)).toMatchObject({ status: "off", note: "median $1.20 · 1 over budget" });
    expect(
      tileStatus(cost, [metric("cost_per_merged_pr", 2.1), metric("cost_per_merged_pr_median_30d", 1.2), metric("over_budget_count", 0)]),
    ).toMatchObject({ status: "on", note: "median $1.20 · 0 over budget" });
    expect(tileStatus(cost, [metric("cost_per_merged_pr", 3.0), metric("cost_per_merged_pr_median_30d", 1.2)])).toMatchObject({
      status: "off",
    });
    expect(tileStatus(share, [])).toEqual({ value: null, status: "nodata" });
    expect(targetText("merge_rate")).toBe("≥ 70%");
  });
});

describe("indicators and badge", () => {
  it("labels, formats and lists thresholds", () => {
    expect(labelOf("repair_rounds_mean")).toBe("Repair rounds per task");
    expect(labelOf("eval_pass_rate_bug")).toBe("Eval pass rate · bug");
    expect(formatIndicator("escalation_rate", 20)).toBe("20.0%");
    expect(formatIndicator("eval_gap_points", -4)).toBe("-4.0 pt");
    expect(formatIndicator("stuck_runs_7d", 0)).toBe("0");
    expect(formatIndicator("repair_rounds_mean", null)).toBe("—");
    expect(thresholdText({ target: 1.5, review: 2.5 })).toBe("target 1.5 · review 2.5");
    expect(total({ plans: 1, questions: 2, decisions: 0, budget: 1 })).toBe(4);
    expect(total(null)).toBe(0);
  });

  it("flags stuck runs as a live status", () => {
    render(<StuckFlag count={2} />);
    expect(screen.getByRole("status").textContent).toContain("2 stuck runs this week");
    cleanup();
    render(<StuckFlag count={0} />);
    expect(screen.getByRole("status").getAttribute("data-stuck")).toBe("0");
  });
});
