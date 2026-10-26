// The console's own per-login limits for the writes it makes to GitHub (TRD §6.1 BFF rows:
// labels 30/min/login, config PRs 10/day/login). A fixed window in process memory; a refused
// request answers 429 with the rule named. The orchestrator limits its own routes itself.

export type Rule = { name: string; limit: number; windowMs: number };

export const LABELS_RULE: Rule = { name: "labels: 30 per minute per login", limit: 30, windowMs: 60_000 };
export const CONFIG_PR_RULE: Rule = { name: "config PRs: 10 per day per login", limit: 10, windowMs: 86_400_000 };

const windows = new Map<string, { startedAt: number; count: number }>();

/** True when `key` may act under `rule` now; counts the call when it does. */
export function allow(key: string, rule: Rule, now = Date.now()): boolean {
  const id = `${rule.name}|${key}`;
  const current = windows.get(id);
  if (!current || now - current.startedAt >= rule.windowMs) {
    windows.set(id, { startedAt: now, count: 1 });
    return true;
  }
  if (current.count >= rule.limit) return false;
  current.count += 1;
  return true;
}

export function resetLimits(): void {
  windows.clear();
}
