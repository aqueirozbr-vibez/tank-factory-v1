// The runtime controls' rules in one place (PRD Capability 5 cost control and kill-switch;
// TRD §6.1 write rows): the kill-switch bound, the concurrency range, the one-time budget raise
// at most 2×, the pill text, and why a control is disabled. Pure; used by the dialogs and the
// pages.

export const KILL_SWITCH_BOUND_S = 60; // SIGTERM lands within 60 s
export const CONCURRENCY_MIN = 1;
export const CONCURRENCY_MAX = 10;
export const BUDGET_RAISE_MAX_FACTOR = 2;

export type KillSwitchNote = { actor: string; at: string; enabled: boolean; reason: string | null };

export function pillText(enabled: boolean | null, live: number | null): string {
  const flag = enabled === null ? "FACTORY_ENABLED=?" : `FACTORY_ENABLED=${enabled ? 1 : 0}`;
  if (live === null) return flag;
  return `${flag} · ${live} live process${live === 1 ? "" : "es"}`;
}

/** Why the kill-switch button is disabled, in the UI context's words, or null when it is live. */
export function killSwitchDisabledReason(input: { allowed: boolean; permission: string | null; enabled: boolean | null; last: KillSwitchNote | null }): string | null {
  if (!input.permission) return "sign in to operate the kill-switch";
  if (!input.allowed) return `no write access (${input.permission})`;
  if (input.enabled === null) return "the factory's state is unknown";
  if (input.enabled === false) {
    return input.last && !input.last.enabled ? `already 0 · stopped by @${input.last.actor} at ${input.last.at}` : "already 0";
  }
  return null;
}

export function countdownText(processes: number, secondsLeft: number): string {
  return `Stopping ${processes} process${processes === 1 ? "" : "es"} · ${Math.max(0, secondsLeft)} s left`;
}

export type RaiseBounds = { min: number; max: number };

/** A raise is one per task and at most 2× the current budget (orchestrator/budget/controls.py). */
export function raiseBounds(current: number): RaiseBounds {
  return { min: Math.round((current + 0.01) * 100) / 100, max: Math.round(current * BUDGET_RAISE_MAX_FACTOR * 100) / 100 };
}

export function raiseDisabledReason(input: { allowed: boolean; permission: string | null; raised: boolean; status: string }): string | null {
  if (!input.permission) return "sign in to raise the budget";
  if (!input.allowed) return `no write access (${input.permission})`;
  if (input.raised) return "raised once already; a second raise is not allowed";
  if (["merged", "cancelled"].includes(input.status)) return `the task is ${input.status}`;
  return null;
}

export function concurrencyValid(n: number): boolean {
  return Number.isInteger(n) && n >= CONCURRENCY_MIN && n <= CONCURRENCY_MAX;
}
