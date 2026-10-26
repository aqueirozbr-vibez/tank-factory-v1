import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import type { Installation } from "@/lib/eval";

import { InstallDryRun, derivationText } from "./InstallDryRun";

afterEach(cleanup);

const BASE: Installation = {
  org: "acme",
  repo: "atlas-api",
  factory_version: "0.1.0",
  config_sha: "cfg1aaaaaaaaaaaa",
  concurrency_n: 3,
  budget_mode: "install",
  per_task_budget_usd: 5,
  daily_cap_usd: 45,
  retention_days: 90,
  factory_enabled: true,
  admissions_paused: false,
  timezone: "UTC",
  export_pubkey: "pk",
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
  spent_today_usd: 0,
  open_incident_seq: null,
  last_kill_switch: null,
  last_dry_run: null,
};

const NOTE: NonNullable<Installation["last_dry_run"]> = {
  at: "2026-10-25T00:00:00Z",
  outcome: "derived",
  issues: [
    { issue: 1, completed: true, cost_usd: 0.32, blocked_reason: null },
    { issue: 2, completed: true, cost_usd: 0.34, blocked_reason: null },
    { issue: 3, completed: false, cost_usd: 0.1, blocked_reason: "repair_cap" },
  ],
  completed: 2,
  required: 2,
  highest_cost_usd: 0.34,
  factor: 2,
  per_task_budget_usd: 5,
  floor_usd: 5,
  reason: "the industry per-task cap 5.00; 2x the highest observed cost 0.34 (0.68) sits below it",
};

describe("the install dry run panel", () => {
  it("prints the derivation with its floor, or without one", () => {
    expect(derivationText(NOTE)).toBe("2 × 0.34 = 0.68 → floor 5.00");
    expect(derivationText({ ...NOTE, highest_cost_usd: 3.1, per_task_budget_usd: 6.2, floor_usd: 5 })).toBe("2 × 3.10 = 6.20");
    expect(derivationText({ ...NOTE, highest_cost_usd: null, reason: "refused: one completion" })).toBe("refused: one completion");
  });

  it("lists the dry-run issues when the trace holds a derivation, and says so when it does not", async () => {
    const { container } = render(<InstallDryRun installation={{ ...BASE, last_dry_run: NOTE }} />);
    expect(screen.getByTestId("derivation").textContent).toBe("2 × 0.34 = 0.68 → floor 5.00");
    const rows = within(screen.getByRole("table", { name: "Dry-run issues" })).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual(["#1yes$0.32—", "#2yes$0.34—", "#3no$0.10repair_cap"]);
    expect(screen.getByText(/2 of 2 needed completed/)).toBeTruthy();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
    cleanup();
    render(<InstallDryRun installation={BASE} />);
    expect(screen.getByText("no dry run recorded in this trace")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
