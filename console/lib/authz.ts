// Generated from authz.yaml by `factory authz`; never hand-edited (TRD §6.2).
export const TABLE_SHA256 = "c511fac38b2f07c847755fbd556f3f1276d04c9e7f66b9c9c55816d6de8ff6f3";
export const PERMISSIONS: ReadonlySet<string> = new Set(["admin", "maintain", "read", "triage", "write"]);
export const ACTIONS: ReadonlySet<string> = new Set(["admissions", "audit_decide", "budget_raise", "cancel", "concurrency", "config_pr", "eval_run", "export", "governed_approve", "kill_switch", "plan_approve", "read", "resume", "risk_override"]);
export const CONDITIONS: ReadonlySet<string> = new Set(["always", "codeowner"]);
const RULES: ReadonlySet<string> = new Set([
  "admin|admissions|always",
  "admin|audit_decide|always",
  "admin|budget_raise|always",
  "admin|cancel|always",
  "admin|concurrency|always",
  "admin|config_pr|always",
  "admin|eval_run|always",
  "admin|export|always",
  "admin|governed_approve|codeowner",
  "admin|kill_switch|always",
  "admin|plan_approve|always",
  "admin|read|always",
  "admin|resume|always",
  "admin|risk_override|always",
  "maintain|admissions|always",
  "maintain|audit_decide|always",
  "maintain|budget_raise|always",
  "maintain|cancel|always",
  "maintain|concurrency|always",
  "maintain|config_pr|always",
  "maintain|eval_run|always",
  "maintain|export|always",
  "maintain|governed_approve|codeowner",
  "maintain|kill_switch|always",
  "maintain|plan_approve|always",
  "maintain|read|always",
  "maintain|resume|always",
  "maintain|risk_override|always",
  "read|read|always",
  "triage|read|always",
  "write|admissions|always",
  "write|audit_decide|always",
  "write|budget_raise|always",
  "write|cancel|always",
  "write|concurrency|always",
  "write|config_pr|always",
  "write|eval_run|always",
  "write|export|always",
  "write|governed_approve|codeowner",
  "write|kill_switch|always",
  "write|plan_approve|always",
  "write|read|always",
  "write|resume|always",
  "write|risk_override|always",
]);

/** Default deny: a row must name permission, action and a condition that holds. */
export function allowed(permission: string, action: string, codeowner = false): boolean {
  if (RULES.has(`${permission}|${action}|always`)) return true;
  return codeowner && RULES.has(`${permission}|${action}|codeowner`);
}
