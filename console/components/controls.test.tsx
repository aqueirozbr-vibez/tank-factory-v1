import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { concurrencyValid, countdownText, killSwitchDisabledReason, pillText, raiseBounds, raiseDisabledReason } from "@/lib/controls";

import { BudgetRaiseDialog } from "./BudgetRaiseDialog";
import { ControlsPanel } from "./ControlsPanel";
import { KillSwitchDialog } from "./KillSwitchDialog";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("control rules", () => {
  it("prints the pill, the reasons a control is disabled, the countdown and the raise bounds", () => {
    expect(pillText(true, 3)).toBe("FACTORY_ENABLED=1 · 3 live processes");
    expect(pillText(false, 1)).toBe("FACTORY_ENABLED=0 · 1 live process");
    expect(pillText(null, null)).toBe("FACTORY_ENABLED=?");
    expect(killSwitchDisabledReason({ allowed: false, permission: null, enabled: true, last: null })).toBe("sign in to operate the kill-switch");
    expect(killSwitchDisabledReason({ allowed: false, permission: "read", enabled: true, last: null })).toBe("no write access (read)");
    expect(killSwitchDisabledReason({ allowed: true, permission: "write", enabled: true, last: null })).toBeNull();
    expect(
      killSwitchDisabledReason({ allowed: true, permission: "write", enabled: false, last: { actor: "mlima", at: "2026-10-24T20:00:00Z", enabled: false, reason: "x" } }),
    ).toBe("already 0 · stopped by @mlima at 2026-10-24T20:00:00Z");
    expect(countdownText(3, 41)).toBe("Stopping 3 processes · 41 s left");
    expect(countdownText(1, -2)).toBe("Stopping 1 process · 0 s left");
    expect(raiseBounds(6)).toEqual({ min: 6.01, max: 12 });
    expect(raiseDisabledReason({ allowed: true, permission: "write", raised: true, status: "running" })).toContain("raised once already");
    expect(raiseDisabledReason({ allowed: true, permission: "write", raised: false, status: "merged" })).toBe("the task is merged");
    expect(raiseDisabledReason({ allowed: true, permission: "write", raised: false, status: "blocked" })).toBeNull();
    expect(concurrencyValid(10)).toBe(true);
    expect(concurrencyValid(11)).toBe(false);
    expect(concurrencyValid(2.5)).toBe(false);
  });
});

describe("kill-switch dialog", () => {
  it("is disabled without write access with the reason, and when already 0 with who and when", async () => {
    const { container } = render(<KillSwitchDialog enabled liveProcesses={3} login="rsouza" permission="read" allowed={false} last={null} />);
    expect(screen.getByTestId("kill-switch-pill").textContent).toBe("FACTORY_ENABLED=1 · 3 live processes");
    expect(screen.getByRole("button", { name: "Stop factory…" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("kill-switch-reason").textContent).toBe("no write access (read)");
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
    cleanup();
    render(<KillSwitchDialog enabled={false} liveProcesses={0} login="rsouza" permission="write" allowed last={{ actor: "mlima", at: "2026-10-24T20:00:00Z", enabled: false, reason: "drill" }} />);
    expect(screen.getByRole("button", { name: "Start factory…" }).hasAttribute("disabled")).toBe(false);
  });

  it("confirms only with a Reason and reads back the events row with the login", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ factory_enabled: false, live_processes: 0, event_seq: 12, elapsed_s: 0.4 })));
    render(<KillSwitchDialog enabled liveProcesses={2} login="rsouza" permission="write" allowed last={null} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop factory…" }));
    const confirm = await screen.findByRole("button", { name: "Stop factory" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    const reason = screen.getByLabelText("Reason");
    expect(document.activeElement).toBe(reason);
    fireEvent.change(reason, { target: { value: "drill" } });
    expect(confirm.hasAttribute("disabled")).toBe(false);
    fireEvent.click(confirm);
    const status = await screen.findByTestId("kill-switch-result");
    expect(status.textContent).toContain("Recorded as events row #12 with your login @rsouza");
    expect(status.textContent).toContain("Factory off · 0 live processes left · 0.4 s");
  });
});

describe("controls panel and budget raise", () => {
  it("needs a changed N and a Reason, marks the open incident, and refuses without write access", () => {
    render(<ControlsPanel concurrency={3} admissionsPaused={true} openIncidentSeq={88} login="rsouza" permission="write" canSetConcurrency canSetAdmissions />);
    const setN = screen.getByRole("button", { name: "Set N" });
    expect(setN.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("New N"), { target: { value: "5" } });
    expect(setN.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getAllByPlaceholderText("Reason (required)")[0]!, { target: { value: "more capacity" } });
    expect(setN.hasAttribute("disabled")).toBe(false);
    expect(screen.getByTestId("open-incident").textContent).toBe("incident #88 open");
    expect(screen.getByRole("button", { name: "Resume admissions" })).toBeTruthy();
    expect(screen.getByLabelText(/clear incident #88/)).toBeTruthy();
    cleanup();
    render(<ControlsPanel concurrency={3} admissionsPaused={false} openIncidentSeq={null} login={null} permission={null} canSetConcurrency={false} canSetAdmissions={false} />);
    expect(screen.getByRole("button", { name: "Set N" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getAllByText("sign in to operate the controls")).toHaveLength(2);
  });

  it("raises once within 2× with a Reason and names why it cannot", async () => {
    render(<BudgetRaiseDialog taskId="acme/atlas-api#7" current={6} raised={true} status="running" login="rsouza" permission="write" allowed />);
    expect(screen.getByRole("button", { name: "Raise budget…" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("raise-reason").textContent).toContain("raised once already");
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ old: 6, new: 12, event_seq: 90 })));
    render(<BudgetRaiseDialog taskId="acme/atlas-api#7" current={6} raised={false} status="running" login="rsouza" permission="write" allowed />);
    fireEvent.click(screen.getByRole("button", { name: "Raise budget…" }));
    const confirm = await screen.findByRole("button", { name: "Raise budget" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("New budget (USD)"), { target: { value: "13" } });
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "long suite" } });
    expect(confirm.hasAttribute("disabled")).toBe(true); // 13 is over 2×
    fireEvent.change(screen.getByLabelText("New budget (USD)"), { target: { value: "12" } });
    expect(confirm.hasAttribute("disabled")).toBe(false);
    fireEvent.click(confirm);
    expect((await screen.findByRole("status")).textContent).toContain("Budget raised $6.00 → $12.00 · events row #90 with @rsouza");
  });
});
