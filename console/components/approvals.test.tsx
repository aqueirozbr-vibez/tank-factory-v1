import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acHoldoutRows,
  approvalState,
  monthText,
  parseMonth,
  parseSections,
  parseTab,
  shiftMonth,
  strata,
  waitingText,
  type AuditItem,
  type PlanItem,
  type QuestionItem,
} from "@/lib/approvals";

import { ApprovalCard } from "./ApprovalCard";
import { AuditDecisionRow } from "./AuditDecisionRow";
import { AuditStrata } from "./AuditTable";
import { QuestionsTable, langOf } from "./QuestionsTable";
import { SpecView } from "./SpecView";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

afterEach(cleanup);

const SPEC = `# Spec 418

## Outcome
Invoices carry a due date.

## Scope
- src/billing/invoice.py

## Non-goals
- Caching of invoice queries.

## Acceptance criteria
1. WHEN an invoice is created THE SYSTEM SHALL set due_date.

## Test plan
AC1 -> tests/acceptance/test_invoice_due_date_holdout.py::test_due_date
`;

function plan(over: Partial<PlanItem> = {}): PlanItem {
  return {
    task_id: "acme/atlas-api#418",
    issue_number: 418,
    title: "Invoice due date",
    risk: "risk:high",
    risk_score: 52,
    governed_path: null,
    spec_path: "specs/418-invoice-due-date.md",
    spec_sha: "b".repeat(40),
    spec_text: SPEC,
    risk_breakdown: null,
    ac_holdout: [
      { item: "AC1", ok: true, note: "tests/acceptance/test_invoice_due_date_holdout.py::test_due_date" },
      { item: "AC2", ok: false, note: "holdout test not found: tests/acceptance/test_missing.py::test_x" },
    ],
    waiting_since: "2026-10-24T10:00:00Z",
    phase_id: "pa1",
    ...over,
  };
}

describe("approvals derivations", () => {
  it("splits the spec into its sections and reads the AC→holdout checks", () => {
    expect(parseSections(SPEC).map((s) => s.title)).toEqual(["Outcome", "Scope", "Non-goals", "Acceptance criteria", "Test plan"]);
    expect(parseSections(SPEC)[2]?.body).toBe("- Caching of invoice queries.");
    expect(parseSections(null)).toEqual([]);
    expect(parseSections("intro line\n## A\nbody")).toEqual([
      { title: "Preamble", body: "intro line" },
      { title: "A", body: "body" },
    ]);
    expect(acHoldoutRows(plan().ac_holdout)).toEqual([
      { ac: "AC1", ok: true, note: "tests/acceptance/test_invoice_due_date_holdout.py::test_due_date" },
      { ac: "AC2", ok: false, note: "holdout test not found: tests/acceptance/test_missing.py::test_x" },
    ]);
    expect(acHoldoutRows([{ nonsense: true }])).toEqual([]);
  });

  it("decides what the viewer may do with a plan and names the reason when not", () => {
    const high = plan();
    expect(approvalState(high, null, null)).toMatchObject({ action: "plan_approve", allowed: false, reason: "sign in to approve" });
    expect(approvalState(high, { login: "x", permission: "read" }, null)).toMatchObject({ allowed: false, reason: "no write access (read)" });
    expect(approvalState(high, { login: "x", permission: "write" }, null)).toMatchObject({ allowed: true, reason: null });
    expect(approvalState(plan({ spec_sha: null }), { login: "x", permission: "write" }, null)).toMatchObject({
      allowed: false,
      reason: "the spec is not committed yet",
    });
    const governed = plan({ risk: "risk:governed", governed_path: "src/billing/" });
    expect(approvalState(governed, { login: "x", permission: "write" }, false)).toMatchObject({
      action: "governed_approve",
      allowed: false,
      reason: "not a CODEOWNER of src/billing/",
    });
    expect(approvalState(governed, { login: "x", permission: "write" }, true)).toMatchObject({ action: "governed_approve", allowed: true });
    expect(approvalState(governed, { login: "x", permission: "admin" }, false)).toMatchObject({ allowed: false, reason: "not a CODEOWNER of src/billing/" });
  });

  it("reads tabs, months, strata and waits", () => {
    expect(parseTab("audit")).toBe("audit");
    expect(parseTab("x")).toBe("plans");
    expect(parseMonth("2026-10")).toBe("2026-10");
    expect(parseMonth("10/2026")).toBeNull();
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(monthText("2026-10")).toBe("October 2026");
    const items: AuditItem[] = [
      { id: "1", task_id: "t1", pr: 1, month: "2026-10", stratum: "risk:low", risk: "risk:low", sampled: true },
      { id: "2", task_id: "t2", pr: 2, month: "2026-10", stratum: "risk:high", risk: "risk:high", sampled: true },
      { id: "3", task_id: "t3", pr: 3, month: "2026-10", stratum: "risk:high", risk: "risk:high", sampled: true },
    ];
    expect(strata(items).map((s) => `${s.stratum}:${s.waiting}:${s.rule}`)).toEqual([
      "risk:low:1:10% sampled",
      "risk:high:2:every PR",
      "risk:governed:0:every PR",
    ]);
    expect(waitingText("2026-10-24T10:00:00Z", new Date("2026-10-24T10:42:00Z"))).toBe("42 min");
    expect(waitingText("2026-10-24T10:00:00Z", new Date("2026-10-24T13:10:00Z"))).toBe("3 h");
    expect(waitingText("2026-10-20T10:00:00Z", new Date("2026-10-24T13:10:00Z"))).toBe("4 d");
    expect(langOf("pt")).toBe("pt-BR");
    expect(langOf("en")).toBe("en");
  });
});

describe("approval card, spec view, questions and audit", () => {
  it("disables the governed button for a non-CODEOWNER and names the reason", async () => {
    const { container } = render(
      <ApprovalCard
        taskId="acme/atlas-api#418"
        issue={418}
        repo="acme/atlas-api"
        login="mlima"
        specSha={"b".repeat(40)}
        holdout={{ present: 2, total: 2 }}
        state={{ action: "governed_approve", allowed: false, reason: "not a CODEOWNER of src/billing/", governedPath: "src/billing/" }}
        issueUrl="https://github.com/acme/atlas-api/issues/418"
      />,
    );
    const button = screen.getByRole("button", { name: "Write factory:plan-approved" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("note").textContent).toBe("Disabled: not a CODEOWNER of src/billing/");
    expect(screen.getByText(/holdout 2 tests all present/)).toBeTruthy();
    expect(screen.getByText("The GitHub label is the approval.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open issue on GitHub ↗" }).getAttribute("href")).toBe("https://github.com/acme/atlas-api/issues/418");
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("writes the label through the console and reads the success line", async () => {
    const calls: Array<{ url: string; body: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: String(init?.body) });
        return Response.json({ labels: ["factory:plan-approved"] });
      }),
    );
    render(
      <ApprovalCard
        taskId="acme/atlas-api#418"
        issue={418}
        repo="acme/atlas-api"
        login="rsouza"
        specSha={"b".repeat(40)}
        holdout={{ present: 1, total: 2 }}
        state={{ action: "plan_approve", allowed: true, reason: null, governedPath: null }}
        issueUrl="#"
      />,
    );
    expect(screen.getByText(/holdout 1 of 2 present/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Write factory:plan-approved" }));
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("factory:plan-approved by @rsouza · just now");
    expect(calls[0]?.url).toBe("/api/console/labels");
    expect(JSON.parse(calls[0]?.body ?? "{}")).toEqual({ task_id: "acme/atlas-api#418", issue: 418, add: "factory:plan-approved", spec_sha: "b".repeat(40) });
    vi.unstubAllGlobals();
  });

  it("shows GitHub's refusal and the spec-changed case, recording nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "github_refused", message: "GitHub refused the label write (403)" }), { status: 403 })));
    render(
      <ApprovalCard taskId="t" issue={1} repo="r" login="l" specSha="s" holdout={{ present: 0, total: 0 }} state={{ action: "plan_approve", allowed: true, reason: null, governedPath: null }} issueUrl="#" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Write factory/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("GitHub refused the write (403)");
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "spec_changed" }), { status: 409 })));
    render(
      <ApprovalCard taskId="t" issue={1} repo="r" login="l" specSha="s" holdout={{ present: 0, total: 0 }} state={{ action: "plan_approve", allowed: true, reason: null, governedPath: null }} issueUrl="#" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Write factory/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("spec sha changed");
    vi.unstubAllGlobals();
  });

  it("renders the spec sections, the AC→holdout table and the questions in their language", async () => {
    const { container } = render(<SpecView item={plan()} specUrl="https://github.com/acme/atlas-api/blob/agent/418/specs/418-invoice-due-date.md" />);
    expect(screen.getByText("Non-goals")).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-ok"))).toEqual(["true", "false"]);
    expect(screen.getByText(/1 of 2 present/)).toBeTruthy();
    let results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
    cleanup();
    const questions: QuestionItem[] = [
      { task_id: "acme/atlas-api#7", issue_number: 7, title: "x", questions: ["Qual é o prazo?"], language: "pt", blocked_at: "2026-10-24T10:00:00Z" },
    ];
    const rendered = render(<QuestionsTable items={questions} repoUrl="https://github.com/acme/atlas-api" />);
    expect(screen.getByText("Qual é o prazo?").closest("td")?.getAttribute("lang")).toBe("pt-BR");
    results = await axe.run(rendered.container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });

  it("counts the strata and needs a decision and a reason before recording", () => {
    render(<AuditStrata items={[{ id: "1", task_id: "t", pr: 1, month: "2026-10", stratum: "risk:governed", risk: "risk:governed", sampled: true }]} month="2026-10" />);
    expect(screen.getByRole("group", { name: "Audit strata for October 2026" }).textContent).toContain("every PR");
    cleanup();
    render(<AuditDecisionRow taskId="t" month="2026-10" canDecide reason={null} />);
    const record = screen.getByRole("button", { name: "Record" });
    expect(record.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByLabelText("agree"));
    expect(record.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("Reason (required)"), { target: { value: "verdict holds" } });
    expect(record.hasAttribute("disabled")).toBe(false);
    cleanup();
    render(<AuditDecisionRow taskId="t" month="2026-10" canDecide={false} reason="no write access (read)" />);
    expect(screen.getByRole("button", { name: "Record" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("no write access (read)")).toBeTruthy();
  });
});
