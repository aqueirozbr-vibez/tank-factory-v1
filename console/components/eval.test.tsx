import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  blockedChanges,
  gapOf,
  gapText,
  latestComplete,
  points,
  regressions,
  resultLine,
  resultOf,
  runsByVersion,
  signed,
  triggerText,
  versionOf,
  type EvalRun,
  type EvalSet,
} from "@/lib/eval";

import { BlockedChange } from "./BlockedChange";
import { EvalComparisonTable, deltaRole } from "./EvalComparisonTable";
import { EvalSetSummary } from "./EvalSetSummary";
import { budgetModeText } from "./InstallDryRun";
import { RunEvalButton } from "./RunEvalButton";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

afterEach(cleanup);

function run(over: Partial<EvalRun> & Pick<EvalRun, "id">): EvalRun {
  return {
    set_id: "s1",
    factory_version: "0.1.0",
    config_sha: "cfg1aaaaaaaa",
    status: "complete",
    trigger: "manual",
    trigger_ref: null,
    started_at: "2026-10-20T10:00:00Z",
    ended_at: "2026-10-20T11:00:00Z",
    pass_rate: 63,
    per_class: { bug: 67, test: 60, docs: 70, feature: 55 },
    baseline_run_id: null,
    delta_points: null,
    blocked: null,
    ...over,
  };
}

const BASELINE = run({ id: "r1" });
const BLOCKED = run({
  id: "r2",
  config_sha: "cfg2bbbbbbbb",
  trigger: "config_pr",
  trigger_ref: "PR #52",
  started_at: "2026-10-22T10:00:00Z",
  pass_rate: 50,
  per_class: { bug: 44, test: 58, docs: 70, feature: 50 },
  baseline_run_id: "r1",
  delta_points: -13,
  blocked: false || true,
});
const RUNNING = run({ id: "r3", status: "running", started_at: "2026-10-24T10:00:00Z", pass_rate: null, per_class: null, ended_at: null });
const RUNS = [BASELINE, BLOCKED, RUNNING];
const SET: EvalSet = { id: "s1", name: "atlas-api eval", repo: "acme/atlas-api", items: 24, reference_pass_rate: null, created_at: "2026-10-01T00:00:00Z" };

describe("eval derivations", () => {
  it("reads results, deltas, triggers and versions", () => {
    expect(resultOf(BLOCKED)).toBe("blocked");
    expect(resultOf(BASELINE)).toBe("passed");
    expect(resultOf(RUNNING)).toBe("running");
    expect(resultOf({ status: "failed", blocked: null })).toBe("failed");
    expect(deltaRole(BLOCKED)).toBe("fail");
    expect(deltaRole({ delta_points: 4, blocked: false })).toBe("pass");
    expect(deltaRole({ delta_points: null, blocked: null })).toBe("neutral");
    expect(signed(-13)).toBe("−13");
    expect(signed(4.4)).toBe("+4");
    expect(signed(null)).toBe("—");
    expect(points(62.6)).toBe("63");
    expect(triggerText("config_pr")).toBe(".factory/ change");
    expect(triggerText("odd")).toBe("odd");
    expect(versionOf(BLOCKED)).toBe("0.1.0 · cfg2bbb");
  });

  it("groups runs by version with the current configuration marked, newest first", () => {
    const groups = runsByVersion(RUNS, [
      { config_sha: "cfg2bbbbbbbb", main_commit: "m2", loaded_at: "2026-10-22T09:00:00Z", current: true, eval: null },
      { config_sha: "cfg1aaaaaaaa", main_commit: "m1", loaded_at: "2026-10-01T09:00:00Z", current: false, eval: null },
    ]);
    expect(groups.map((g) => [g.key, g.current, g.runs.map((r) => r.id)])).toEqual([
      ["0.1.0 · cfg1aaa", false, ["r3", "r1"]],
      ["0.1.0 · cfg2bbb", true, ["r2"]],
    ]);
  });

  it("names the blocked changes and the regression behind each", () => {
    const blocked = blockedChanges(RUNS);
    expect(blocked.map((b) => b.run.id)).toEqual(["r2"]);
    expect(blocked[0]?.baseline?.id).toBe("r1");
    expect(regressions(BLOCKED, BASELINE).map((r) => `${r.label}:${r.drop}`)).toEqual(["bugs:23", "small features:5", "tests:2"]);
    expect(resultLine(BLOCKED, BASELINE)).toBe("Overall 63 → 50 (−13 pt). bugs 67 → 44.");
    expect(resultLine(BASELINE, null)).toBe("Overall 63.");
  });

  it("finds the latest complete run and the gap to the pilot", () => {
    expect(latestComplete(RUNS, "s1")?.id).toBe("r2");
    expect(latestComplete(RUNS, "other")).toBeNull();
    expect(gapOf(SET, BLOCKED)).toBeNull();
    expect(gapOf({ reference_pass_rate: 63 }, { pass_rate: 50 })).toEqual({ points: 13, within: false });
    expect(gapText(gapOf({ reference_pass_rate: 63 }, { pass_rate: 50 }))).toBe("13 points below the pilot (over 10)");
    expect(gapText(gapOf({ reference_pass_rate: 60 }, { pass_rate: 55 }))).toBe("5 points below the pilot");
    expect(gapText(gapOf({ reference_pass_rate: 60 }, { pass_rate: 65 }))).toBe("5 points above the pilot");
    expect(gapText(null)).toBe("no reference pass rate");
    expect(budgetModeText("install")).toContain("3-issue dry run");
  });
});

describe("eval screens", () => {
  it("reads a blocked run as blocked with its delta in the fail role", async () => {
    const { container } = render(<EvalComparisonTable runs={RUNS} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-result"))).toEqual(["passed", "blocked", "running"]);
    const blockedRow = rows[1];
    expect(within(blockedRow!).getByTestId("result-text").textContent).toBe("blocked");
    const delta = blockedRow!.querySelector('[data-role="fail"]');
    expect(delta?.textContent).toBe("−13");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Version",
      "Trigger",
      "Overall",
      "bugs",
      "tests",
      "docs",
      "small features",
      "Δ overall",
      "Result",
    ]);
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("lists the blocked change with its result line and the set summary with its gap", async () => {
    render(<BlockedChange runs={RUNS} />);
    expect(screen.getByText("Overall 63 → 50 (−13 pt). bugs 67 → 44.")).toBeTruthy();
    expect(screen.getByText("PR #52")).toBeTruthy();
    cleanup();
    const { container } = render(<EvalSetSummary sets={[{ ...SET, reference_pass_rate: 63 }]} runs={RUNS} />);
    expect(screen.getByRole("article", { name: "Eval set atlas-api eval" }).textContent).toContain("13 points below the pilot (over 10)");
    expect(screen.getByRole("img", { name: /Gate: fail/ })).toBeTruthy();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("disables the run button with the reason and below 20 items", () => {
    render(<RunEvalButton sets={[SET]} allowed={false} reason="no write access (read)" />);
    expect(screen.getByRole("button", { name: "Run eval" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("no write access (read)")).toBeTruthy();
    cleanup();
    render(<RunEvalButton sets={[{ ...SET, items: 12 }]} allowed reason={null} />);
    expect(screen.getByRole("button", { name: "Run eval" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("an eval set needs at least 20 items")).toBeTruthy();
    cleanup();
    render(<RunEvalButton sets={[SET]} allowed reason={null} />);
    expect(screen.getByRole("button", { name: "Run eval" }).hasAttribute("disabled")).toBe(false);
  });
});
