import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { cacheInit } from "@/lib/api";
import {
  applyEdits,
  branchName,
  evalFor,
  parseTab,
  pathKind,
  pullBody,
  qualityRows,
  reasonOf,
  rosterRows,
  type OpenChange,
} from "@/lib/config";
import type { EvalRun } from "@/lib/eval";

import { ProtectedPathsTable, RosterTable } from "./ConfigTables";
import { ConfigChangeCard, diffLines, evalLine } from "./OpenChanges";
import { ProposeChangeDialog } from "./ProposeChangeDialog";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

afterEach(cleanup);

const ROSTER = {
  planner: { harness: "claude_code", model: "model-a", thinking: "high", tools: ["Read"], writes: ["specs/"], max_turns: 40, phase_cap_usd: 1.5, timeout_s: 1800 },
  builder: { harness: "claude_code", model: "model-a", thinking: "medium", tools: ["Read", "Bash"], writes: ["**"], max_turns: 80, phase_cap_usd: 3, timeout_s: 7200 },
  reviewer: { harness: "codex", model: "model-b", tools: ["Read", "Glob", "Grep"], writes: [], max_turns: 40, phase_cap_usd: 1.5, timeout_s: 2700 },
};

function run(over: Partial<EvalRun> & Pick<EvalRun, "id">): EvalRun {
  return {
    set_id: "s1",
    factory_version: "0.1.0",
    config_sha: "cfg1aaaaaaaa",
    status: "complete",
    trigger: "config_pr",
    trigger_ref: "52",
    started_at: "2026-10-22T10:00:00Z",
    ended_at: "2026-10-22T11:00:00Z",
    pass_rate: 50,
    per_class: { bug: 44, test: 58, docs: 70, feature: 50 },
    baseline_run_id: "r1",
    delta_points: -13,
    blocked: true,
    ...over,
  };
}

const BASELINE = run({ id: "r1", trigger: "manual", trigger_ref: null, started_at: "2026-10-20T10:00:00Z", pass_rate: 63, per_class: { bug: 67, test: 60, docs: 70, feature: 55 }, baseline_run_id: null, delta_points: null, blocked: null });
const BLOCKED = run({ id: "r2" });
const RUNS = [BASELINE, BLOCKED];

function change(over: Partial<OpenChange> = {}): OpenChange {
  return {
    pull: { number: 52, html_url: "https://github.com/acme/atlas-api/pull/52", title: "factory-config: .factory/roster.yaml", body: "Reason: cheaper builder\n\nOpened by @rsouza", head: "factory-config/rsouza/20261022T100000Z", user: "rsouza", created_at: "2026-10-22T10:00:00Z" },
    files: [{ filename: ".factory/roster.yaml", status: "modified", patch: "@@ -8,1 +8,1 @@\n-  model: model-a\n+  model: model-c\n   thinking: medium" }],
    reason: "cheaper builder",
    eval: BLOCKED,
    ...over,
  };
}

describe("configuration derivations", () => {
  it("applies find/replace edits only when each find occurs exactly once and something changes", () => {
    const text = "planner:\n  model: a\nbuilder:\n  model: a\nreviewer:\n  model: b\n";
    expect(applyEdits(text, [{ find: "  model: b", replace: "  model: c" }])).toEqual({ ok: true, text: text.replace("  model: b", "  model: c") });
    expect(applyEdits(text, [{ find: "  model: a", replace: "  model: c" }])).toMatchObject({ ok: false, reason: expect.stringContaining("more than once") });
    expect(applyEdits(text, [{ find: "  model: z", replace: "x" }])).toMatchObject({ ok: false, reason: expect.stringContaining("not in the file") });
    expect(applyEdits(text, [{ find: "  model: b", replace: "  model: b" }])).toMatchObject({ ok: false, reason: "the edits change nothing" });
  });

  it("names the branch, the PR body and the reason read back from it", () => {
    expect(branchName("rsouza", new Date("2026-10-22T10:00:00.123Z"))).toBe("factory-config/rsouza/20261022T100000Z");
    const body = pullBody({ login: "rsouza", reason: "cheaper builder", file: ".factory/roster.yaml" });
    expect(body.startsWith("Reason: cheaper builder\n")).toBe(true);
    expect(body).toContain("Not applied until merged");
    expect(reasonOf(body)).toBe("cheaper builder");
    expect(reasonOf(null)).toBeNull();
  });

  it("matches a PR with its config_pr eval run by number, reads the roster and the tabs", () => {
    expect(evalFor({ number: 52 }, RUNS)?.id).toBe("r2");
    expect(evalFor({ number: 52 }, [run({ id: "r9", trigger_ref: "PR #52" })])?.id).toBe("r9");
    expect(evalFor({ number: 53 }, RUNS)).toBeNull();
    const rows = rosterRows(ROSTER, { reviewer: "openai" });
    expect(rows.map((r) => `${r.role}:${r.model}:${r.vendor ?? "-"}`)).toEqual(["planner:model-a:-", "builder:model-a:-", "reviewer:model-b:openai"]);
    expect(rosterRows(null)).toEqual([]);
    expect(qualityRows({ format: ["uv", "run", "ruff", "format"], lint: ["uv", "run", "ruff", "check"], test: ["uv", "run", "pytest"], typecheck: ["uv", "run", "mypy"], full_suite: ["uv", "run", "pytest"] }).map((r) => r.gate)).toEqual([
      "format",
      "lint",
      "typecheck",
      "test",
      "full_suite",
    ]);
    expect(pathKind(".factory/roster.yaml")).toBe("prd");
    expect(pathKind("tests/acceptance/")).toBe("prd");
    expect(pathKind("infra/")).toBe("added");
    expect(parseTab("budgets")).toBe("budgets");
    expect(parseTab(undefined)).toBe("roster");
    expect(cacheInit("/api/v1/config", { tags: ["config"] })).toEqual({ cache: "force-cache", next: { tags: ["config"] } });
    expect(cacheInit("/api/v1/tasks", { tags: ["x"] })).toEqual({ cache: "no-store" });
    expect(cacheInit("/api/v1/approvals/plans", {})).toEqual({ cache: "no-store" });
  });
});

describe("configuration screens", () => {
  it("shows a blocked change with its eval line in the error role and its diff", async () => {
    const { container } = render(<ConfigChangeCard change={change()} runs={RUNS} />);
    const card = screen.getByRole("article", { name: "Change PR #52" });
    expect(card.getAttribute("data-eval")).toBe("fail");
    expect(screen.getByTestId("eval-line").textContent).toBe("Eval blocked the change. Overall 63 → 50 (−13 pt). bugs 67 → 44.");
    expect(card.textContent).toContain("Opened by @rsouza · cheaper builder · not applied until merged");
    expect(diffLines(change().files[0]?.patch ?? null).map((l) => l.kind)).toEqual(["hunk", "del", "add", "ctx"]);
    expect(evalLine(change({ eval: run({ id: "r3", status: "running", blocked: null, pass_rate: null }) }), RUNS)).toEqual({ text: "Eval running", role: "info" });
    expect(evalLine(change({ eval: null }), RUNS).role).toBe("neutral");
    expect(evalLine(change({ eval: run({ id: "r4", blocked: false, delta_points: 2, pass_rate: 65 }) }), RUNS)).toMatchObject({ role: "pass" });
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("renders the roster and protected paths tables and disables proposing without write access", async () => {
    const { container } = render(<RosterTable rows={rosterRows(ROSTER)} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-role"))).toEqual(["planner", "builder", "reviewer"]);
    let results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
    cleanup();
    render(<ProtectedPathsTable paths={[".factory/", "infra/"]} />);
    expect(within(screen.getByRole("table")).getAllByRole("row").slice(1).map((r) => r.getAttribute("data-kind"))).toEqual(["prd", "added"]);
    cleanup();
    const dialog = render(<ProposeChangeDialog files={[]} allowed={false} reason="no write access (read)" />);
    expect(screen.getByRole("button", { name: "Propose change…" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("no write access (read)")).toBeTruthy();
    results = await axe.run(dialog.container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
